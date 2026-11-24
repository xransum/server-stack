"""
server.py — MCP server entry point for spotbook.

Wires together:
  - MCPServer (mcp SDK) with custom auth routes
  - Starlette app for HTTP (Streamable HTTP transport at /mcp)
  - SQLite DB
  - Google Places client
  - SpotbookAuthProvider (GitHub OAuth + MCP token lifecycle)
  - Backup scheduler
  - All MCP tools

Transport notes:
  - TransportSecuritySettings: required or every request returns 421
  - proxy-headers: uvicorn must run with --proxy-headers when behind NPM
  - /mcp is the Streamable HTTP endpoint (not /sse — that is the legacy transport)

Run:
  uvicorn spotbook.server:app --host 0.0.0.0 --port 8086 --proxy-headers
    --forwarded-allow-ips='*'
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from mcp.server import MCPServer
from mcp.server.auth.provider import (
    AccessToken,
    OAuthAuthorizationServerProvider,
    TokenVerifier,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from .auth import SpotbookAuthProvider
from .backup import start_backup_scheduler
from .db import Db
from .google import GooglePlacesClient
from .tools import (
    tool_add_area,
    tool_check_hours,
    tool_discover,
    tool_list_areas,
    tool_list_spots,
    tool_log_visit,
    tool_save_spot,
    tool_travel_time,
    tool_update_area,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set.")
    return val


def _build_config() -> dict[str, Any]:
    return {
        "public_base_url": _require_env("SPOTBOOK_PUBLIC_BASE_URL").rstrip("/"),
        "db_path": os.environ.get("SPOTBOOK_DB_PATH", "/data/spotbook.db"),
        "backup_dir": os.environ.get("SPOTBOOK_BACKUP_DIR", "/data/backups"),
        "google_api_key": _require_env("GOOGLE_MAPS_API_KEY"),
        "github_client_id": _require_env("SPOTBOOK_GITHUB_CLIENT_ID"),
        "github_client_secret": _require_env("SPOTBOOK_GITHUB_CLIENT_SECRET"),
        "allowed_github_user_id": int(_require_env("SPOTBOOK_GITHUB_ALLOWED_USER_ID")),
        "enterprise_cap": int(os.environ.get("SPOTBOOK_ENTERPRISE_MONTHLY_CAP", "900")),
        "pro_cap": int(os.environ.get("SPOTBOOK_PRO_MONTHLY_CAP", "4500")),
        "discord_webhook": os.environ.get("SPOTBOOK_DISCORD_WEBHOOK", ""),
    }


# ---------------------------------------------------------------------------
# MCP SDK adapter — wrap SpotbookAuthProvider into the SDK interfaces
# ---------------------------------------------------------------------------

class SpotbookTokenVerifier(TokenVerifier):
    """Verify bearer tokens against SpotbookAuthProvider."""

    def __init__(self, provider: SpotbookAuthProvider, resource_url: str) -> None:
        self._provider = provider
        self._resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        info = self._provider.verify_access_token(token)
        if info is None:
            return None
        return AccessToken(
            token=token,
            client_id=info["client_id"],
            scopes=(info.get("scope") or "").split() if info.get("scope") else [],
            resource=self._resource_url,
        )


# ---------------------------------------------------------------------------
# Custom Starlette routes for OAuth endpoints
# ---------------------------------------------------------------------------

def build_oauth_routes(auth: SpotbookAuthProvider, public_base_url: str) -> list[Route]:
    """Return Starlette Route objects for all OAuth and discovery endpoints."""

    mcp_url = f"{public_base_url}/mcp"
    issuer_url = public_base_url

    async def oauth_protected_resource(request: Request) -> Response:
        return JSONResponse({
            "resource": mcp_url,
            "authorization_servers": [issuer_url + "/"],
            "bearer_methods_supported": ["header"],
        })

    async def oauth_authorization_server(request: Request) -> Response:
        return JSONResponse({
            "issuer": issuer_url,
            "authorization_endpoint": f"{issuer_url}/authorize",
            "token_endpoint": f"{issuer_url}/token",
            "registration_endpoint": f"{issuer_url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    async def register(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        try:
            result = auth.register_client(body)
            return JSONResponse(result, status_code=201)
        except ValueError as e:
            return JSONResponse({"error": "invalid_client_metadata", "error_description": str(e)}, status_code=400)

    async def authorize(request: Request) -> Response:
        p = request.query_params
        client_id = p.get("client_id", "")
        redirect_uri = p.get("redirect_uri", "")
        code_challenge = p.get("code_challenge", "")
        code_challenge_method = p.get("code_challenge_method", "S256")
        scope = p.get("scope")
        state = p.get("state")

        if not client_id or not redirect_uri or not code_challenge:
            return HTMLResponse(
                "<h1>Bad Request</h1><p>Missing required OAuth parameters.</p>",
                status_code=400,
            )

        try:
            github_url = auth.begin_authorize(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope,
                state=state,
            )
        except ValueError as e:
            return HTMLResponse(
                f"<h1>Authorization Error</h1><p>{e}</p>",
                status_code=400,
            )

        return RedirectResponse(github_url, status_code=302)

    async def github_callback(request: Request) -> Response:
        p = request.query_params
        code = p.get("code", "")
        state = p.get("state", "")

        if not code or not state:
            return HTMLResponse(
                "<h1>Bad Request</h1><p>Missing code or state from GitHub.</p>",
                status_code=400,
            )

        # Get client IP (behind proxy, trust X-Forwarded-For via uvicorn --proxy-headers)
        client_ip = request.client.host if request.client else "unknown"

        try:
            redirect_url = auth.handle_github_callback(
                code=code,
                state=state,
                client_ip=client_ip,
            )
        except PermissionError as e:
            status = 429 if "Too many" in str(e) else 403
            return HTMLResponse(
                f"<h1>{'Too Many Requests' if status == 429 else 'Forbidden'}</h1>"
                f"<p>{e}</p>",
                status_code=status,
            )
        except ValueError as e:
            return HTMLResponse(
                f"<h1>Authorization Failed</h1><p>{e}</p>",
                status_code=400,
            )

        return RedirectResponse(redirect_url, status_code=302)

    async def token(request: Request) -> Response:
        try:
            body = await request.form()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        grant_type = body.get("grant_type", "")

        if grant_type == "authorization_code":
            client_id = body.get("client_id", "")
            code = body.get("code", "")
            redirect_uri = body.get("redirect_uri", "")
            code_verifier = body.get("code_verifier", "")

            if not all([client_id, code, redirect_uri, code_verifier]):
                return JSONResponse({"error": "invalid_request"}, status_code=400)

            try:
                result = auth.exchange_authorization_code(
                    client_id=client_id,
                    code=code,
                    redirect_uri=redirect_uri,
                    code_verifier=code_verifier,
                )
                return JSONResponse(result)
            except ValueError as e:
                error = str(e)
                return JSONResponse(
                    {"error": error if error == "invalid_grant" else "invalid_grant"},
                    status_code=400,
                )

        elif grant_type == "refresh_token":
            client_id = body.get("client_id", "")
            refresh_token = body.get("refresh_token", "")

            if not all([client_id, refresh_token]):
                return JSONResponse({"error": "invalid_request"}, status_code=400)

            try:
                result = auth.exchange_refresh_token(
                    client_id=client_id,
                    refresh_token=refresh_token,
                )
                return JSONResponse(result)
            except ValueError as e:
                error = str(e)
                return JSONResponse(
                    {"error": error if error == "invalid_grant" else "invalid_grant"},
                    status_code=400,
                )

        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return [
        Route("/.well-known/oauth-protected-resource", oauth_protected_resource),
        Route("/.well-known/oauth-authorization-server", oauth_authorization_server),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/github/callback", github_callback, methods=["GET"]),
        Route("/token", token, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    """Build the full Starlette ASGI application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = _build_config()

    db = Db(cfg["db_path"])
    google = GooglePlacesClient(
        api_key=cfg["google_api_key"],
        db=db,
        enterprise_cap=cfg["enterprise_cap"],
        pro_cap=cfg["pro_cap"],
    )
    auth_provider = SpotbookAuthProvider(
        db=db,
        public_base_url=cfg["public_base_url"],
        github_client_id=cfg["github_client_id"],
        github_client_secret=cfg["github_client_secret"],
        allowed_github_user_id=cfg["allowed_github_user_id"],
    )

    mcp_url = f"{cfg['public_base_url']}/mcp"
    token_verifier = SpotbookTokenVerifier(auth_provider, mcp_url)

    mcp = MCPServer(
        "spotbook",
        token_verifier=token_verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(cfg["public_base_url"]),
            resource_server_url=AnyHttpUrl(mcp_url),
            required_scopes=[],
            validate_token_resource=True,
        ),
    )

    # -----------------------------------------------------------------------
    # Register tools
    # -----------------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_areas() -> dict:
        """
        List all configured geographic areas (e.g. Manassas VA, Redmond WA).
        Each area has a name, timezone, center coordinates, and search radius.
        """
        return tool_list_areas(db)

    @mcp.tool()
    def add_area(
        name: str,
        timezone: str,
        center_lat: float,
        center_lng: float,
        radius_km: float,
    ) -> dict:
        """
        Add a new geographic area for organizing spots.
        timezone: IANA timezone string (e.g. 'America/New_York').
        center_lat/center_lng: center of the area for biasing searches.
        radius_km: search radius in kilometers.
        """
        return tool_add_area(db, name, timezone, center_lat, center_lng, radius_km)

    @mcp.tool()
    def update_area(
        area_id: str,
        name: str | None = None,
        timezone: str | None = None,
        center_lat: float | None = None,
        center_lng: float | None = None,
        radius_km: float | None = None,
    ) -> dict:
        """
        Update an existing area's name, timezone, center, or radius.
        Only provide the fields you want to change.
        """
        return tool_update_area(
            db, area_id,
            name=name, timezone=timezone,
            center_lat=center_lat, center_lng=center_lng, radius_km=radius_km,
        )

    @mcp.tool()
    def save_spot(
        area_id: str,
        place_id: str | None = None,
        name: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        category: str = "other",
        occasions: list[str] | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> dict:
        """
        Save a place to the spotbook.
        If place_id (Google place ID) is provided, details are fetched from Google.
        If place_id is None, name is required (manual spot — no hours data).
        category: restaurant | bar | coffee | activity | outdoors | venue | other
        occasions: list of contexts (e.g. ['date', 'friends', 'solo', 'family', 'group'])
        tags: free-form list of tags for personal organization
        """
        return tool_save_spot(
            db, google, area_id,
            place_id=place_id, name=name, address=address,
            lat=lat, lng=lng, category=category,
            occasions=occasions, tags=tags, notes=notes,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_spots(
        area_id: str | None = None,
        occasions: list[str] | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        near: str | None = None,
        max_distance_km: float | None = None,
        open_at: str | None = None,
        min_rating: float | None = None,
        max_price_level: int | None = None,
    ) -> dict:
        """
        List saved spots with optional filters.
        Each result includes open_status (open/closed/unknown) based on stored hours.
        near: spot UUID, Google place_id, or 'lat,lng' string.
        open_at: ISO-8601 datetime to check hours against (default: now).
        max_price_level: 0=free 1=inexpensive 2=moderate 3=expensive 4=very expensive.
        min_rating: minimum Google rating (0.0 to 5.0).
        """
        return tool_list_spots(
            db, google,
            area_id=area_id, occasions=occasions, category=category, tags=tags,
            near=near, max_distance_km=max_distance_km, open_at=open_at,
            min_rating=min_rating, max_price_level=max_price_level,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def check_hours(
        spot_id: str | None = None,
        place_id: str | None = None,
        at: str | None = None,
    ) -> dict:
        """
        Check if a spot is open at a given time. Returns open/closed/unknown.
        Never guesses — returns unknown if no hours data is available.
        at: ISO-8601 datetime string (default: now).
        Provide either spot_id (internal UUID) or place_id (Google place ID).
        """
        return tool_check_hours(db, google, spot_id=spot_id, place_id=place_id, at=at)

    @mcp.tool()
    def log_visit(
        spot_id: str,
        rating: int | None = None,
        notes: str | None = None,
        occasion: str | None = None,
        with_whom: str | None = None,
    ) -> dict:
        """
        Record a visit to a saved spot.
        rating: 1 (poor) to 5 (excellent).
        occasion: context for the visit (e.g. 'date', 'friends', 'solo').
        with_whom: free text, e.g. 'Sarah' or 'work team'.
        """
        return tool_log_visit(db, spot_id, rating=rating, notes=notes,
                              occasion=occasion, with_whom=with_whom)

    @mcp.tool(annotations={"readOnlyHint": True})
    def discover(
        area_id: str,
        query: str,
        occasion: str | None = None,
        max_results: int = 10,
    ) -> dict:
        """
        Search Google for places matching the query in the given area.
        Returns candidates with name, address, rating, price level, and an
        already_saved flag. Does not save anything automatically — use save_spot()
        to add a result to the spotbook.
        occasion: optional context hint for the search query (does not filter results).
        """
        return tool_discover(db, google, area_id, query, occasion=occasion,
                             max_results=max_results)

    @mcp.tool(annotations={"readOnlyHint": True})
    def travel_time(
        origin: str,
        destination: str,
        mode: str = "DRIVE",
        depart_at: str | None = None,
    ) -> dict:
        """
        [Phase 2 — not yet available] Get travel time between two locations.
        """
        return tool_travel_time()

    # -----------------------------------------------------------------------
    # Build MCP streamable HTTP app with transport security
    # -----------------------------------------------------------------------

    public_hostname = cfg["public_base_url"].removeprefix("https://").removeprefix("http://")
    security = TransportSecuritySettings(
        allowed_hosts=[public_hostname, f"{public_hostname}:*"],
    )

    mcp_app = mcp.streamable_http_app(transport_security=security)

    # -----------------------------------------------------------------------
    # Lifespan — start session manager + backup scheduler
    # -----------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            start_backup_scheduler(
                db_path=cfg["db_path"],
                backup_dir=cfg["backup_dir"],
                discord_webhook=cfg["discord_webhook"],
            )
            log.info("spotbook started — %s", mcp_url)
            yield
        log.info("spotbook shutdown.")

    # -----------------------------------------------------------------------
    # Assemble routes: OAuth + health first, then MCP mount (catches all)
    # -----------------------------------------------------------------------

    oauth_routes = build_oauth_routes(auth_provider, cfg["public_base_url"])

    app = Starlette(
        routes=oauth_routes + [Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )

    return app


# Module-level app object for uvicorn
app = create_app()
