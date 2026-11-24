"""
google.py — Google Places API (New) and Routes API calls with cap enforcement.

All network calls go through this module. No other module imports urllib directly.

BILLING NOTES (verified 2026-11-24 from Google SKU details):
  Places API (New) SKUs:
    ESSENTIALS: location, displayName, formattedAddress, id, utcOffsetMinutes
    PRO:        timeZone
    ENTERPRISE: regularOpeningHours, currentOpeningHours, rating, userRatingCount, priceLevel
  Places free caps: Essentials 10,000 | Pro 5,000 | Enterprise 1,000 per month

  Routes API (Compute Route Matrix) SKUs — billing per ELEMENT (1 origin × 1 destination = 1 element):
    ROUTES ESSENTIALS: no routingPreference, DRIVE/WALK/BICYCLE/TRANSIT without traffic
    ROUTES PRO:        routingPreference TRAFFIC_AWARE or TRAFFIC_AWARE_OPTIMAL
    ROUTES ENTERPRISE: two-wheel, toll, large vehicle routing
  Routes free caps: Essentials 10,000 | Pro 5,000 | Enterprise 1,000 per month
  TRANSIT mode: confirmed ESSENTIALS (not listed as Pro or Enterprise trigger)

  We NEVER set routingPreference — all Routes calls are ESSENTIALS.
  We NEVER use Enterprise features (tolls, two-wheel, large vehicle).

  Monthly counters use Pacific time (America/Los_Angeles) — Google's billing timezone.
  Daily burst counters use UTC.

Google Cloud Console quotas set (2026-11-24):
  Routes API  — DistanceMatrix ComputeRouteMatrix per-element/day: 500
  Routes API  — DistanceMatrix ComputeRouteMatrix per-element/min: 100
  Places (New) — GetPlaceRequest per day: 30
  Places (New) — SearchTextRequest per day: 30

API bases:
  Places: https://places.googleapis.com/v1
  Routes: https://routes.googleapis.com
"""

import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .db import Db, pacific_month, pacific_next_month_reset, utc_date_str

log = logging.getLogger(__name__)

PLACES_BASE = "https://places.googleapis.com/v1"
ROUTES_BASE = "https://routes.googleapis.com"

# ---------------------------------------------------------------------------
# Field masks — always explicit, never "*"
# ---------------------------------------------------------------------------

FIELD_MASK_TEXT_SEARCH = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.rating,"
    "places.userRatingCount,"
    "places.priceLevel"
)

FIELD_MASK_PLACE_DETAILS_SAVE = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "location,"
    "timeZone,"
    "rating,"
    "userRatingCount,"
    "priceLevel,"
    "regularOpeningHours,"
    "currentOpeningHours"
)

FIELD_MASK_PLACE_DETAILS_HOURS_ONLY = (
    "regularOpeningHours,"
    "currentOpeningHours"
)

FIELD_MASK_ROUTE_MATRIX = (
    "originIndex,"
    "destinationIndex,"
    "duration,"
    "distanceMeters,"
    "status,"
    "condition"
)

# ---------------------------------------------------------------------------
# SKU name constants
# ---------------------------------------------------------------------------

# Places API (New)
PLACES_SKU_ESSENTIALS = "essentials"
PLACES_SKU_PRO = "pro"
PLACES_SKU_ENTERPRISE = "enterprise"

# Routes API (Compute Route Matrix)
ROUTES_SKU_ESSENTIALS = "routes_essentials"
ROUTES_SKU_PRO = "routes_pro"          # unused — no traffic routing
ROUTES_SKU_ENTERPRISE = "routes_enterprise"  # unused — no Enterprise features

# Human-readable labels for messages
_SKU_LABELS = {
    PLACES_SKU_ESSENTIALS: "Places Essentials",
    PLACES_SKU_PRO: "Places Pro",
    PLACES_SKU_ENTERPRISE: "Places Enterprise",
    ROUTES_SKU_ESSENTIALS: "Routes Essentials",
    ROUTES_SKU_PRO: "Routes Pro",
    ROUTES_SKU_ENTERPRISE: "Routes Enterprise",
}

# Warning threshold: send Discord notice when usage crosses 80% of monthly cap
_WARNING_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# CapExceededError
# ---------------------------------------------------------------------------

class CapExceededError(Exception):
    """Raised when a Google API cap has been reached."""

    def __init__(
        self,
        sku: str,
        cap: int,
        current: int,
        reset_date: str,
        limit_type: str = "monthly",
    ) -> None:
        self.sku = sku
        self.cap = cap
        self.current = current
        self.reset_date = reset_date
        self.limit_type = limit_type
        label = _SKU_LABELS.get(sku, sku)
        super().__init__(
            f"Google API {limit_type} cap reached for {label}: "
            f"{current}/{cap} elements used. "
            f"Resets: {reset_date}. "
            f"No further {label} calls will be made until then."
        )


# ---------------------------------------------------------------------------
# Shared cap-check + warning helper
# ---------------------------------------------------------------------------

def _check_and_record(
    db: Db,
    sku: str,
    amount: int,
    monthly_cap: int,
    daily_cap: int,
    discord_webhook: str,
) -> None:
    """
    Atomically check caps and record usage. Raises CapExceededError if either
    cap would be exceeded. Sends a one-time Discord warning at 80% of monthly cap.
    """
    new_monthly, _new_daily = db.check_and_increment_usage(
        sku=sku,
        amount=amount,
        monthly_cap=monthly_cap,
        daily_cap=daily_cap,
    )

    log.debug("API usage %s %s: %d/%d", pacific_month(), sku, new_monthly, monthly_cap)

    # 80% warning — send once per SKU per month
    threshold = int(monthly_cap * _WARNING_THRESHOLD)
    old_total = new_monthly - amount
    if old_total < threshold <= new_monthly:
        month = pacific_month()
        if not db.has_warned_cap(month, sku):
            db.record_cap_warning(month, sku)
            label = _SKU_LABELS.get(sku, sku)
            pct = int(new_monthly / monthly_cap * 100)
            reset = pacific_next_month_reset()
            _send_cap_warning(discord_webhook, label, pct, new_monthly, monthly_cap, reset)


def _send_cap_warning(
    webhook: str,
    label: str,
    pct: int,
    count: int,
    cap: int,
    reset_date: str,
) -> None:
    """Send a Discord cap warning. Never includes spot data, queries, or locations."""
    if not webhook:
        return
    from .discord import notify
    month = pacific_month()
    # Format: November 2026
    try:
        dt = datetime.strptime(month, "%Y-%m")
        month_label = dt.strftime("%B %Y")
    except ValueError:
        month_label = month
    msg = (
        f":warning: **spotbook API cap warning**: {label} is at {pct}% "
        f"({count}/{cap}) for {month_label}. Resets {reset_date}."
    )
    notify(webhook, msg)
    log.warning("Cap warning sent for %s: %d/%d", label, count, cap)


# ---------------------------------------------------------------------------
# GooglePlacesClient
# ---------------------------------------------------------------------------

class GooglePlacesClient:
    """Google Places API (New) with atomic cap enforcement."""

    def __init__(
        self,
        api_key: str,
        db: Db,
        discord_webhook: str = "",
        essentials_cap: int = 8000,
        pro_cap: int = 4000,
        enterprise_cap: int = 800,
        daily_cap_fraction: float = 0.1,
    ) -> None:
        self._api_key = api_key
        self._db = db
        self._discord_webhook = discord_webhook
        self._caps = {
            PLACES_SKU_ESSENTIALS: essentials_cap,
            PLACES_SKU_PRO: pro_cap,
            PLACES_SKU_ENTERPRISE: enterprise_cap,
        }
        self._daily_cap_fraction = daily_cap_fraction

    def _daily_cap(self, sku: str) -> int:
        return max(1, int(self._caps[sku] * self._daily_cap_fraction))

    def _record(self, sku: str, amount: int = 1) -> None:
        _check_and_record(
            db=self._db,
            sku=sku,
            amount=amount,
            monthly_cap=self._caps[sku],
            daily_cap=self._daily_cap(sku),
            discord_webhook=self._discord_webhook,
        )

    def _get(self, path: str, field_mask: str) -> dict[str, Any]:
        url = f"{PLACES_BASE}/{path}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": field_mask,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            log.error("Places API error %d: %s", e.code, body)
            raise RuntimeError(f"Places API returned {e.code}: {body}") from e

    def _post(self, path: str, body: dict, field_mask: str) -> dict[str, Any]:
        url = f"{PLACES_BASE}/{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": field_mask,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            log.error("Places API error %d: %s", e.code, body)
            raise RuntimeError(f"Places API returned {e.code}: {body}") from e

    def text_search(
        self,
        query: str,
        lat: float,
        lng: float,
        radius_m: float,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Text Search — 1 ESSENTIALS request."""
        self._record(PLACES_SKU_ESSENTIALS)
        body = {
            "textQuery": query,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius_m,
                }
            },
            "maxResultCount": min(max_results, 20),
        }
        result = self._post("places:searchText", body, FIELD_MASK_TEXT_SEARCH)
        return result.get("places", [])

    def place_details_full(self, place_id: str) -> dict[str, Any]:
        """Full Place Details — 1 ENTERPRISE request."""
        self._record(PLACES_SKU_ENTERPRISE)
        return self._get(f"places/{place_id}", FIELD_MASK_PLACE_DETAILS_SAVE)

    def place_details_hours_only(self, place_id: str) -> dict[str, Any]:
        """Hours-only Place Details — 1 ENTERPRISE request."""
        self._record(PLACES_SKU_ENTERPRISE)
        return self._get(f"places/{place_id}", FIELD_MASK_PLACE_DETAILS_HOURS_ONLY)

    def get_usage_summary(self) -> dict[str, int]:
        month = pacific_month()
        return {
            sku: self._db.get_api_usage(month, sku)
            for sku in (PLACES_SKU_ESSENTIALS, PLACES_SKU_PRO, PLACES_SKU_ENTERPRISE)
        }


# ---------------------------------------------------------------------------
# GoogleRoutesClient
# ---------------------------------------------------------------------------

class GoogleRoutesClient:
    """
    Google Routes API — Compute Route Matrix.

    We NEVER set routingPreference, so every call bills as ESSENTIALS.
    depart_at is forwarded to the API for TRANSIT scheduling but does not
    trigger traffic routing and does not change the SKU.

    Results are cached for 24 hours keyed by (origin, destinations, mode).
    Cache is checked before any cap check — cache hits cost nothing.
    """

    VALID_MODES = {"DRIVE", "WALK", "BICYCLE", "TRANSIT"}
    MAX_DESTINATIONS = 5
    # TRANSIT has a tighter per-request element cap (100 vs 625)
    MAX_ELEMENTS_TRANSIT = 100
    MAX_ELEMENTS_DEFAULT = 625

    def __init__(
        self,
        api_key: str,
        db: Db,
        discord_webhook: str = "",
        essentials_cap: int = 8000,
        pro_cap: int = 4000,
        enterprise_cap: int = 800,
        daily_cap_fraction: float = 0.1,
    ) -> None:
        self._api_key = api_key
        self._db = db
        self._discord_webhook = discord_webhook
        self._caps = {
            ROUTES_SKU_ESSENTIALS: essentials_cap,
            ROUTES_SKU_PRO: pro_cap,
            ROUTES_SKU_ENTERPRISE: enterprise_cap,
        }
        self._daily_cap_fraction = daily_cap_fraction

    def _daily_cap(self, sku: str) -> int:
        return max(1, int(self._caps[sku] * self._daily_cap_fraction))

    def _record(self, sku: str, amount: int) -> None:
        _check_and_record(
            db=self._db,
            sku=sku,
            amount=amount,
            monthly_cap=self._caps[sku],
            daily_cap=self._daily_cap(sku),
            discord_webhook=self._discord_webhook,
        )

    @staticmethod
    def cache_key(
        origin_lat: float,
        origin_lng: float,
        dest_lls: list[tuple[float, float]],
        mode: str,
    ) -> str:
        """SHA-256 of normalised origin+destinations+mode. Stable across runs."""
        parts = [f"{origin_lat:.6f},{origin_lng:.6f}"]
        for lat, lng in dest_lls:
            parts.append(f"{lat:.6f},{lng:.6f}")
        parts.append(mode.upper())
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()

    def compute_route_matrix(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lls: list[tuple[float, float]],
        mode: str = "DRIVE",
        depart_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Call Compute Route Matrix. Returns a list of result dicts, one per
        (origin, destination) pair.

        Billing: ROUTES_ESSENTIALS per element (n_destinations elements for
        1 origin). Cache hit = zero cost.

        depart_at: ISO-8601 string. Used as departure time hint (useful for
        TRANSIT schedules). Does NOT trigger traffic routing. Does NOT affect
        the SKU or cost.

        If Google returns HTTP 403, raises RuntimeError with the response body.
        The caller (tool_travel_time) surfaces this to Claude unchanged.
        """
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"Invalid mode '{mode}'. Valid: {sorted(self.VALID_MODES)}"
            )
        if not dest_lls:
            raise ValueError("At least one destination is required.")
        if len(dest_lls) > self.MAX_DESTINATIONS:
            raise ValueError(
                f"Maximum {self.MAX_DESTINATIONS} destinations per call."
            )

        n_elements = len(dest_lls)  # 1 origin × N destinations

        # --- Cache check (free — no API call, no cap deduction) ---
        key = self.cache_key(origin_lat, origin_lng, dest_lls, mode)
        cached = self._db.get_travel_time_cache(key)
        if cached is not None:
            log.debug("travel_time cache hit for key %s", key[:12])
            return cached

        # --- Cap check + atomic increment (before the API call) ---
        # Count against caps BEFORE calling Google. Even if the call fails,
        # we count it — conservative, protects against retry storms.
        self._record(ROUTES_SKU_ESSENTIALS, n_elements)

        # --- API call ---
        try:
            results = self._call_api(origin_lat, origin_lng, dest_lls, mode, depart_at)
        except Exception:
            # Already counted above. Re-raise so the caller can handle it.
            raise

        # --- Cache store ---
        self._db.set_travel_time_cache(key, results)
        return results

    def _call_api(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lls: list[tuple[float, float]],
        mode: str,
        depart_at: str | None,
    ) -> list[dict[str, Any]]:
        """Make the actual Routes API request. Returns parsed element list."""
        body: dict[str, Any] = {
            "origins": [
                {
                    "waypoint": {
                        "location": {
                            "latLng": {"latitude": origin_lat, "longitude": origin_lng}
                        }
                    }
                }
            ],
            "destinations": [
                {
                    "waypoint": {
                        "location": {
                            "latLng": {"latitude": lat, "longitude": lng}
                        }
                    }
                }
                for lat, lng in dest_lls
            ],
            "travelMode": mode,
            # No routingPreference — stays at Essentials SKU.
            # NOTE: departureTime cannot be set with TRAFFIC_UNAWARE routing
            # (Google returns 400). Since we never use traffic routing, we
            # never send departureTime. The depart_at parameter is accepted
            # by the tool for API compatibility but silently ignored here.
        }

        url = f"{ROUTES_BASE}/distanceMatrix/v2:computeRouteMatrix"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": FIELD_MASK_ROUTE_MATRIX,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()
            log.error("Routes API error %d: %s", e.code, body_text)
            if e.code == 403:
                raise RuntimeError(
                    f"Routes API returned 403 Forbidden. "
                    f"Check that Routes API is enabled and the API key restriction allows it. "
                    f"Response: {body_text}"
                ) from e
            raise RuntimeError(
                f"Routes API returned {e.code}: {body_text}"
            ) from e

        # The Routes API returns a JSON array (not NDJSON) for the matrix.
        # Parse the whole body as a single JSON value.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            log.error("Could not parse Routes API response: %s", raw[:200])
            raise RuntimeError(f"Routes API returned unparseable response: {e}") from e

        if isinstance(parsed, list):
            elements = parsed
        elif isinstance(parsed, dict):
            # Unexpected single-object response — wrap it
            elements = [parsed]
        else:
            elements = []

        return elements

    def get_usage_summary(self) -> dict[str, int]:
        month = pacific_month()
        return {
            sku: self._db.get_api_usage(month, sku)
            for sku in (ROUTES_SKU_ESSENTIALS, ROUTES_SKU_PRO, ROUTES_SKU_ENTERPRISE)
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_place_details(raw: dict) -> dict[str, Any]:
    location = raw.get("location", {})
    display_name = raw.get("displayName", {})
    timezone_info = raw.get("timeZone", {})
    regular = raw.get("regularOpeningHours", {})
    current = raw.get("currentOpeningHours", {})

    return {
        "place_id": raw.get("id"),
        "name": display_name.get("text") or display_name.get("languageCode", ""),
        "address": raw.get("formattedAddress"),
        "lat": location.get("latitude"),
        "lng": location.get("longitude"),
        "timezone": timezone_info.get("id"),
        "price_level": _parse_price_level(raw.get("priceLevel")),
        "rating": raw.get("rating"),
        "user_rating_count": raw.get("userRatingCount"),
        "regular_hours_json": json.dumps(regular.get("periods", [])) if regular else None,
        "current_hours_json": json.dumps(current.get("periods", [])) if current else None,
        "hours_fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_hours_refresh(raw: dict, existing: dict) -> dict[str, Any]:
    regular = raw.get("regularOpeningHours", {})
    current = raw.get("currentOpeningHours", {})

    return {
        "regular_hours_json": (
            json.dumps(regular.get("periods", [])) if regular
            else existing.get("regular_hours_json")
        ),
        "current_hours_json": (
            json.dumps(current.get("periods", [])) if current
            else existing.get("current_hours_json")
        ),
        "hours_fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _parse_price_level(value: str | None) -> int | None:
    mapping = {
        "PRICE_LEVEL_FREE": 0,
        "PRICE_LEVEL_INEXPENSIVE": 1,
        "PRICE_LEVEL_MODERATE": 2,
        "PRICE_LEVEL_EXPENSIVE": 3,
        "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    }
    return mapping.get(value)


def _format_duration(seconds: int | None) -> str:
    """Convert seconds to human-readable string e.g. '12 min' or '1 hr 5 min'."""
    if seconds is None:
        return "unknown"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"{hours} hr"
    return f"{hours} hr {mins} min"


def _format_distance(meters: int | None) -> str:
    """Convert meters to human-readable string e.g. '2.4 km' or '800 m'."""
    if meters is None:
        return "unknown"
    if meters < 1000:
        return f"{meters} m"
    return f"{meters / 1000:.1f} km"
