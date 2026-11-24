"""
auth.py — OAuthAuthorizationServerProvider implementation for spotbook.

Auth flow:
  1. Claude hits /mcp → 401 with WWW-Authenticate pointing at discovery doc
  2. Claude reads /.well-known/oauth-protected-resource → finds authorization_servers
  3. Claude reads /.well-known/oauth-authorization-server → gets DCR + PKCE endpoints
  4. Claude calls POST /register → gets client_id (no secret; public client)
  5. Claude redirects browser to GET /authorize?...
  6. /authorize validates redirect_uri, then redirects to GitHub
  7. GitHub redirects to GET /github/callback?code=...
  8. We verify GitHub identity (numeric user ID), issue auth_code, redirect to Claude
  9. Claude calls POST /token with auth_code + PKCE verifier → gets access + refresh tokens
  10. Claude calls /mcp with Bearer <access_token> → verified against SQLite hash

Token storage: only SHA-256 hex hashes stored in SQLite. Raw tokens never persisted.

PKCE: S256 required. We verify code_challenge == BASE64URL(SHA256(code_verifier)).

Rate limiting: auth_failures table tracks GitHub callback failures per IP.
  - 5 failures per IP in a 15-minute window → 429 Too Many Requests
  - Prune old failures on startup and hourly.

Redirect URI: only https://claude.ai/api/mcp/auth_callback accepted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .discord import notify as discord_notify

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
AUTH_CODE_TTL_SECONDS = 60
ACCESS_TOKEN_TTL_SECONDS = 3600          # 1 hour
REFRESH_TOKEN_ABSOLUTE_DAYS = 90
RATE_LIMIT_FAILURES = 5
RATE_LIMIT_WINDOW_MINUTES = 15
GITHUB_API_USER = "https://api.github.com/user"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _pkce_verify(code_verifier: str, code_challenge: str) -> bool:
    """Verify S256 PKCE: BASE64URL(SHA256(verifier)) == challenge."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return _constant_time_eq(computed, code_challenge)


def _generate_token() -> str:
    """Generate a 256-bit random token (hex string)."""
    return secrets.token_hex(32)


def _generate_code() -> str:
    """Generate a 160-bit random authorization code."""
    return secrets.token_urlsafe(20)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _refresh_expires_iso() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_ABSOLUTE_DAYS)
    ).isoformat()


def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# GitHub OAuth helpers
# ---------------------------------------------------------------------------

def github_authorize_url(
    client_id: str,
    state: str,
    redirect_uri: str,
) -> str:
    """Build the GitHub OAuth authorize URL."""
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user",
        "state": state,
        "allow_signup": "false",
    })
    return f"{GITHUB_AUTHORIZE_URL}?{params}"


def github_exchange_code(
    code: str,
    github_client_id: str,
    github_client_secret: str,
    redirect_uri: str,
) -> str:
    """Exchange GitHub code for a GitHub access token. Returns the raw token."""
    data = urllib.parse.urlencode({
        "client_id": github_client_id,
        "client_secret": github_client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }).encode()

    req = urllib.request.Request(
        GITHUB_TOKEN_URL,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"GitHub token exchange failed: {e}") from e

    if "error" in result:
        raise ValueError(
            f"GitHub OAuth error: {result.get('error_description', result['error'])}"
        )
    token = result.get("access_token")
    if not token:
        raise ValueError("GitHub did not return an access_token.")
    return token


def github_get_user_id(github_token: str) -> int:
    """Return the numeric GitHub user ID for the given token."""
    req = urllib.request.Request(
        GITHUB_API_USER,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            user = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"GitHub user lookup failed: {e}") from e

    uid = user.get("id")
    if not uid:
        raise ValueError("Could not read GitHub user ID.")
    return int(uid)


# ---------------------------------------------------------------------------
# OAuth provider — SQLite-backed
# ---------------------------------------------------------------------------

class SpotbookAuthProvider:
    """
    Self-contained OAuth 2.0 Authorization Server implementation.

    Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK.
    Uses SQLite for all state. GitHub is the identity layer.
    """

    def __init__(
        self,
        db: Any,
        public_base_url: str,
        github_client_id: str,
        github_client_secret: str,
        allowed_github_user_id: int,
        discord_webhook: str = "",
    ) -> None:
        self._db = db
        self._base_url = public_base_url.rstrip("/")
        self._github_client_id = github_client_id
        self._github_client_secret = github_client_secret
        self._allowed_user_id = allowed_github_user_id
        self._discord_webhook = discord_webhook

        # In-memory PKCE/state store for active authorization flows
        # Maps state → {client_id, redirect_uri, code_challenge, scope}
        # Short-lived; lost on restart (acceptable — user just re-auths)
        self._pending_flows: dict[str, dict] = {}

    # -----------------------------------------------------------------------
    # DCR — Dynamic Client Registration
    # -----------------------------------------------------------------------

    def register_client(self, client_metadata: dict) -> dict:
        """
        Register a new OAuth client. Returns client info including client_id.
        Only https://claude.ai/api/mcp/auth_callback is accepted as redirect_uri.
        """
        redirect_uris = client_metadata.get("redirect_uris", [])
        if not redirect_uris:
            raise ValueError("redirect_uris is required.")

        for uri in redirect_uris:
            if uri != ALLOWED_REDIRECT_URI:
                raise ValueError(
                    f"Redirect URI '{uri}' is not allowed. "
                    f"Only {ALLOWED_REDIRECT_URI} is permitted."
                )

        client_id = f"claude-{secrets.token_hex(8)}"
        now_iso = _now_iso()

        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO oauth_clients
                    (client_id, client_secret_hash, redirect_uris_json, client_name, registered_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (
                    client_id,
                    json.dumps(redirect_uris),
                    client_metadata.get("client_name"),
                    now_iso,
                ),
            )

        log.info("Registered new OAuth client: %s", client_id)
        client_name = client_metadata.get("client_name") or "unknown"
        discord_notify(
            self._discord_webhook,
            f":door: **spotbook DCR**: new client registered — `{client_name}` (`{client_id}`)",
        )
        return {
            "client_id": client_id,
            "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
            "redirect_uris": redirect_uris,
            "client_name": client_metadata.get("client_name"),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "code_challenge_methods_supported": ["S256"],
        }

    def get_client(self, client_id: str) -> dict | None:
        with self._db.conn() as c:
            row = c.execute(
                "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    # -----------------------------------------------------------------------
    # /authorize — start the GitHub flow
    # -----------------------------------------------------------------------

    def begin_authorize(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str | None,
        state: str | None,
    ) -> str:
        """
        Validate the authorization request and return a GitHub authorize URL.
        The browser is redirected here.
        """
        # Validate client
        client = self.get_client(client_id)
        if not client:
            raise ValueError(f"Unknown client_id: {client_id}")

        # Validate redirect_uri
        if redirect_uri != ALLOWED_REDIRECT_URI:
            raise ValueError(f"Invalid redirect_uri: {redirect_uri}")

        # Validate PKCE
        if code_challenge_method != "S256":
            raise ValueError("Only S256 code_challenge_method is supported.")
        if not code_challenge:
            raise ValueError("code_challenge is required.")

        # Store pending flow
        flow_state = secrets.token_urlsafe(16)
        self._pending_flows[flow_state] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "original_state": state,
        }

        # Build GitHub callback URL for us (not for Claude)
        our_callback = f"{self._base_url}/github/callback"
        return github_authorize_url(
            client_id=self._github_client_id,
            state=flow_state,
            redirect_uri=our_callback,
        )

    # -----------------------------------------------------------------------
    # /github/callback — receive GitHub code, verify identity, issue auth code
    # -----------------------------------------------------------------------

    def handle_github_callback(
        self,
        code: str,
        state: str,
        client_ip: str,
    ) -> str:
        """
        Exchange GitHub code for token, verify user ID, issue MCP auth code.
        Returns the redirect URL for the browser (back to Claude).
        """
        # Rate limit check
        self._check_rate_limit(client_ip)

        # Retrieve pending flow
        flow = self._pending_flows.pop(state, None)
        if not flow:
            self._record_failure(client_ip)
            raise ValueError("Invalid or expired OAuth state.")

        # Exchange GitHub code
        our_callback = f"{self._base_url}/github/callback"
        try:
            gh_token = github_exchange_code(
                code, self._github_client_id, self._github_client_secret, our_callback
            )
        except Exception as e:
            self._record_failure(client_ip)
            raise ValueError(f"GitHub exchange failed: {e}") from e

        # Verify user ID
        try:
            user_id = github_get_user_id(gh_token)
        except Exception as e:
            self._record_failure(client_ip)
            raise ValueError(f"GitHub user lookup failed: {e}") from e

        if user_id != self._allowed_user_id:
            self._record_failure(client_ip)
            log.warning(
                "GitHub login attempt by unauthorized user ID %d (allowed: %d)",
                user_id,
                self._allowed_user_id,
            )
            discord_notify(
                self._discord_webhook,
                f":no_entry: **spotbook unauthorized login attempt** from IP `{client_ip}` "
                f"(GitHub user ID: `{user_id}`)",
            )
            raise PermissionError("Not authorized.")

        # Issue MCP authorization code
        auth_code = _generate_code()
        expires_at = _expires_iso(AUTH_CODE_TTL_SECONDS)

        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO auth_codes
                    (code, client_id, redirect_uri, code_challenge, scope, expires_at, used)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    auth_code,
                    flow["client_id"],
                    flow["redirect_uri"],
                    flow["code_challenge"],
                    flow["scope"],
                    expires_at,
                ),
            )

        log.info("Issued auth code for client %s", flow["client_id"])

        # Build redirect back to Claude
        params = {"code": auth_code}
        if flow.get("original_state"):
            params["state"] = flow["original_state"]
        redirect = f"{ALLOWED_REDIRECT_URI}?" + urllib.parse.urlencode(params)
        return redirect

    # -----------------------------------------------------------------------
    # /token — exchange auth code or refresh token
    # -----------------------------------------------------------------------

    def exchange_authorization_code(
        self,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict:
        """Exchange an authorization code for access + refresh tokens."""
        with self._db.conn() as c:
            row = c.execute(
                "SELECT * FROM auth_codes WHERE code = ?", (code,)
            ).fetchone()

        if not row:
            raise ValueError("invalid_grant")

        auth_code = dict(row)

        # Single use check
        if auth_code["used"]:
            raise ValueError("invalid_grant")

        # Expiry check
        if _is_expired(auth_code["expires_at"]):
            raise ValueError("invalid_grant")

        # client_id check
        if auth_code["client_id"] != client_id:
            raise ValueError("invalid_grant")

        # redirect_uri check
        if auth_code["redirect_uri"] != redirect_uri:
            raise ValueError("invalid_grant")

        # PKCE check
        if not _pkce_verify(code_verifier, auth_code["code_challenge"]):
            raise ValueError("invalid_grant")

        # Mark used
        with self._db.conn() as c:
            c.execute("UPDATE auth_codes SET used = 1 WHERE code = ?", (code,))

        return self._issue_token_pair(client_id, auth_code.get("scope"))

    def exchange_refresh_token(
        self,
        client_id: str,
        refresh_token: str,
    ) -> dict:
        """
        Exchange a refresh token for a new access + refresh token pair.
        Rotates the refresh token — the old one is invalidated immediately.
        Returns invalid_grant if the token is expired, revoked, or reused.
        """
        token_hash = _sha256_hex(refresh_token)

        with self._db.conn() as c:
            row = c.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND token_type = 'refresh'",
                (token_hash,),
            ).fetchone()

        if not row:
            raise ValueError("invalid_grant")

        token = dict(row)

        if token["revoked"]:
            # Reused refresh token — this is a security event (potential token theft)
            # Revoke all tokens for this client as a precaution
            log.warning(
                "Reused refresh token detected for client %s — revoking all tokens",
                client_id,
            )
            self._revoke_all_for_client(client_id)
            raise ValueError("invalid_grant")

        if token["client_id"] != client_id:
            raise ValueError("invalid_grant")

        if _is_expired(token["expires_at"]):
            raise ValueError("invalid_grant")

        # Revoke old refresh token (and paired access token)
        with self._db.conn() as c:
            c.execute(
                "UPDATE tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,)
            )
            if token.get("access_token_hash"):
                c.execute(
                    "UPDATE tokens SET revoked = 1 WHERE token_hash = ?",
                    (token["access_token_hash"],),
                )

        return self._issue_token_pair(client_id, token.get("scope"))

    def verify_access_token(self, raw_token: str) -> dict | None:
        """
        Verify a bearer token. Returns token info dict if valid, None otherwise.
        """
        token_hash = _sha256_hex(raw_token)
        with self._db.conn() as c:
            row = c.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND token_type = 'access'",
                (token_hash,),
            ).fetchone()

        if not row:
            return None

        token = dict(row)
        if token["revoked"]:
            return None
        if _is_expired(token["expires_at"]):
            return None

        return token

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _issue_token_pair(self, client_id: str, scope: str | None) -> dict:
        """Issue a new access + refresh token pair. Returns the token response dict."""
        raw_access = _generate_token()
        raw_refresh = _generate_token()
        access_hash = _sha256_hex(raw_access)
        refresh_hash = _sha256_hex(raw_refresh)
        now_iso = _now_iso()
        access_expires = _expires_iso(ACCESS_TOKEN_TTL_SECONDS)
        refresh_expires = _refresh_expires_iso()

        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO tokens
                    (token_hash, token_type, client_id, scope, expires_at,
                     refresh_token_hash, access_token_hash, revoked)
                VALUES (?, 'access', ?, ?, ?, ?, NULL, 0)
                """,
                (access_hash, client_id, scope, access_expires, refresh_hash),
            )
            c.execute(
                """
                INSERT INTO tokens
                    (token_hash, token_type, client_id, scope, expires_at,
                     refresh_token_hash, access_token_hash, revoked)
                VALUES (?, 'refresh', ?, ?, ?, NULL, ?, 0)
                """,
                (refresh_hash, client_id, scope, refresh_expires, access_hash),
            )

        return {
            "access_token": raw_access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token": raw_refresh,
            "scope": scope or "",
        }

    def _revoke_all_for_client(self, client_id: str) -> None:
        with self._db.conn() as c:
            c.execute(
                "UPDATE tokens SET revoked = 1 WHERE client_id = ?", (client_id,)
            )

    def _check_rate_limit(self, ip: str) -> None:
        since = (
            datetime.now(timezone.utc) - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        ).isoformat()
        count = self._db.count_auth_failures(ip, since)
        if count >= RATE_LIMIT_FAILURES:
            log.warning("Rate limit exceeded for IP %s", ip)
            raise PermissionError(
                f"Too many failed attempts. Try again after {RATE_LIMIT_WINDOW_MINUTES} minutes."
            )

    def _record_failure(self, ip: str) -> None:
        self._db.record_auth_failure(ip, "/github/callback", _now_iso())
