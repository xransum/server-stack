"""
google.py — Google Places API (New) calls with field masking and cap enforcement.

All network calls go through this module. No other module imports urllib directly.

SKU classification (verified 2026-11-23):
  ESSENTIALS: location, displayName, utcOffsetMinutes, formattedAddress, id
  PRO:        timeZone, utcOffsetMinutes, displayName (some overlap — be conservative)
  ENTERPRISE: regularOpeningHours, currentOpeningHours, rating, userRatingCount,
              priceLevel

We only bill ENTERPRISE on save_spot() and hours refresh.
Text Search (discover) uses ESSENTIALS + minimal PRO fields only.

API base: https://places.googleapis.com/v1
"""

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .db import Db

log = logging.getLogger(__name__)

PLACES_BASE = "https://places.googleapis.com/v1"

# Field masks — always explicit, never "*"
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

# SKU names for the api_usage table
SKU_ESSENTIALS = "essentials"
SKU_PRO = "pro"
SKU_ENTERPRISE = "enterprise"


class CapExceededError(Exception):
    """Raised when a Google API monthly cap has been reached."""
    def __init__(self, sku: str, cap: int, current: int):
        self.sku = sku
        self.cap = cap
        self.current = current
        super().__init__(
            f"Google API cap reached for {sku}: {current}/{cap} calls this month. "
            f"No further {sku} calls will be made until next month."
        )


class GooglePlacesClient:
    """Thin wrapper around the Google Places API (New) with cap enforcement."""

    def __init__(
        self,
        api_key: str,
        db: Db,
        enterprise_cap: int = 900,
        pro_cap: int = 4500,
    ) -> None:
        self._api_key = api_key
        self._db = db
        self._enterprise_cap = enterprise_cap
        self._pro_cap = pro_cap

    def _current_month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _check_cap(self, sku: str) -> None:
        """Raise CapExceededError if the monthly cap for this SKU is reached."""
        cap = {
            SKU_ENTERPRISE: self._enterprise_cap,
            SKU_PRO: self._pro_cap,
            SKU_ESSENTIALS: 9500,  # hard-coded safety margin below 10k
        }.get(sku, 9500)

        month = self._current_month()
        current = self._db.get_api_usage(month, sku)
        if current >= cap:
            raise CapExceededError(sku, cap, current)

    def _record_usage(self, sku: str, amount: int = 1) -> None:
        month = self._current_month()
        new_total = self._db.increment_api_usage(month, sku, amount)
        log.debug("API usage %s %s: %d", month, sku, new_total)

    def _get(self, path: str, field_mask: str) -> dict[str, Any]:
        """Make a GET request to the Places API."""
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
            log.error("Google Places API error %d: %s", e.code, body)
            raise RuntimeError(f"Google Places API returned {e.code}: {body}") from e

    def _post(self, path: str, body: dict, field_mask: str) -> dict[str, Any]:
        """Make a POST request to the Places API."""
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
            log.error("Google Places API error %d: %s", e.code, body)
            raise RuntimeError(f"Google Places API returned {e.code}: {body}") from e

    def text_search(
        self,
        query: str,
        lat: float,
        lng: float,
        radius_m: float,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Text Search biased to a geographic area. Uses ESSENTIALS + minimal fields.
        Each call costs 1 ESSENTIALS SKU request.
        """
        self._check_cap(SKU_ESSENTIALS)

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
        self._record_usage(SKU_ESSENTIALS)
        return result.get("places", [])

    def place_details_full(self, place_id: str) -> dict[str, Any]:
        """
        Fetch full place details including hours, rating, priceLevel.
        Costs 1 ENTERPRISE SKU request (most expensive fields included).
        """
        self._check_cap(SKU_ENTERPRISE)

        result = self._get(
            f"places/{place_id}",
            FIELD_MASK_PLACE_DETAILS_SAVE,
        )
        self._record_usage(SKU_ENTERPRISE)
        return result

    def place_details_hours_only(self, place_id: str) -> dict[str, Any]:
        """
        Fetch only opening hours for a known place. Used for weekly refresh.
        Costs 1 ENTERPRISE SKU request (hours fields are ENTERPRISE).
        """
        self._check_cap(SKU_ENTERPRISE)

        result = self._get(
            f"places/{place_id}",
            FIELD_MASK_PLACE_DETAILS_HOURS_ONLY,
        )
        self._record_usage(SKU_ENTERPRISE)
        return result

    def get_usage_summary(self) -> dict[str, int]:
        """Return current month usage for all SKUs."""
        month = self._current_month()
        return {
            sku: self._db.get_api_usage(month, sku)
            for sku in (SKU_ESSENTIALS, SKU_PRO, SKU_ENTERPRISE)
        }


# ---------------------------------------------------------------------------
# Parsing helpers — convert raw Google API response fields to our schema
# ---------------------------------------------------------------------------

def parse_place_details(raw: dict) -> dict[str, Any]:
    """
    Convert a Google Place Details response to spotbook's internal format.
    Returns a dict suitable for INSERT into the spots table.
    """
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
        "timezone": timezone_info.get("id"),  # IANA zone id
        "price_level": _parse_price_level(raw.get("priceLevel")),
        "rating": raw.get("rating"),
        "user_rating_count": raw.get("userRatingCount"),
        "regular_hours_json": json.dumps(regular.get("periods", [])) if regular else None,
        "current_hours_json": json.dumps(current.get("periods", [])) if current else None,
        "hours_fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_hours_refresh(raw: dict, existing: dict) -> dict[str, Any]:
    """
    Extract updated hours fields from a place_details_hours_only() response.
    Returns only the fields to be updated.
    """
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
    """Convert Google's PRICE_LEVEL_* enum string to 0-4 int."""
    mapping = {
        "PRICE_LEVEL_FREE": 0,
        "PRICE_LEVEL_INEXPENSIVE": 1,
        "PRICE_LEVEL_MODERATE": 2,
        "PRICE_LEVEL_EXPENSIVE": 3,
        "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    }
    return mapping.get(value)
