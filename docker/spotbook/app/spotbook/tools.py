"""
tools.py — All MCP tool definitions for spotbook.

Tools are registered on the MCPServer instance created in server.py.
This module only contains the tool logic; the server wires them up.

Occasion-neutral throughout: no date/romantic assumptions in names,
descriptions, or parameter docs.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "restaurant", "bar", "coffee", "activity", "outdoors", "venue", "other"
}

HOURS_REFRESH_DAYS = 7


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in km between two lat/lng points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Hours refresh helper
# ---------------------------------------------------------------------------

def _should_refresh_hours(hours_fetched_at: str | None) -> bool:
    if not hours_fetched_at:
        return True
    try:
        fetched = datetime.fromisoformat(hours_fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - fetched).days
        return age_days >= HOURS_REFRESH_DAYS
    except ValueError:
        return True


def _maybe_refresh_hours(spot: dict, google_client: Any, db: Any) -> dict:
    """
    Refresh hours for a spot if they are older than HOURS_REFRESH_DAYS.
    Returns the (possibly updated) spot dict.
    If the spot has no place_id (manual spot), returns unchanged.
    If the Google cap is exceeded, logs a warning and returns unchanged.
    """
    if not spot.get("place_id"):
        return spot
    if not _should_refresh_hours(spot.get("hours_fetched_at")):
        return spot

    from .google import CapExceededError, parse_hours_refresh
    try:
        raw = google_client.place_details_hours_only(spot["place_id"])
        updates = parse_hours_refresh(raw, spot)
        with db.conn() as c:
            c.execute(
                """
                UPDATE spots SET
                    regular_hours_json = :regular_hours_json,
                    current_hours_json = :current_hours_json,
                    hours_fetched_at   = :hours_fetched_at
                WHERE id = :id
                """,
                {**updates, "id": spot["id"]},
            )
        return {**spot, **updates}
    except CapExceededError as e:
        log.warning("Hours refresh skipped: %s", e)
        return spot
    except Exception as e:
        log.warning("Hours refresh failed for %s: %s", spot.get("place_id"), e)
        return spot


# ---------------------------------------------------------------------------
# open_status helper
# ---------------------------------------------------------------------------

def _open_status(spot: dict, at: datetime | None = None) -> dict[str, Any]:
    """
    Return open_status dict for a spot.
    If the spot is manual (no place_id), always returns unknown.
    """
    if not spot.get("place_id"):
        return {
            "open_status": "unknown",
            "regular_hours_only": False,
            "reason": "Manual spot — no hours data available.",
        }

    from .hours import check_hours
    query_dt = at or datetime.now(timezone.utc)
    return check_hours(
        query_dt=query_dt,
        area_timezone=spot.get("area_timezone") or "UTC",
        spot_timezone=spot.get("timezone"),
        regular_hours_json=spot.get("regular_hours_json"),
        current_hours_json=spot.get("current_hours_json"),
        hours_fetched_at=spot.get("hours_fetched_at"),
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_list_areas(db: Any) -> dict:
    """List all configured areas."""
    with db.conn() as c:
        rows = c.execute("SELECT * FROM areas ORDER BY name").fetchall()
    areas = [dict(r) for r in rows]
    return {
        "areas": areas,
        "count": len(areas),
        "summary": f"{len(areas)} area(s) configured.",
    }


def tool_add_area(
    db: Any,
    name: str,
    timezone: str,
    center_lat: float,
    center_lng: float,
    radius_km: float,
) -> dict:
    """Add a new area."""
    area_id = f"area-{uuid.uuid4().hex[:12]}"
    with db.conn() as c:
        try:
            c.execute(
                """
                INSERT INTO areas (id, name, timezone, center_lat, center_lng, radius_km)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (area_id, name, timezone, center_lat, center_lng, radius_km),
            )
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                raise ValueError(f"An area named '{name}' already exists.") from e
            raise
    return {
        "id": area_id,
        "name": name,
        "timezone": timezone,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "radius_km": radius_km,
        "summary": f"Area '{name}' added.",
    }


def tool_update_area(db: Any, area_id: str, **kwargs: Any) -> dict:
    """Update an existing area's fields."""
    allowed = {"name", "timezone", "center_lat", "center_lng", "radius_km"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        raise ValueError("No valid fields to update.")

    sets = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [area_id]

    with db.conn() as c:
        row = c.execute("SELECT id FROM areas WHERE id = ?", (area_id,)).fetchone()
        if not row:
            raise ValueError(f"Area '{area_id}' not found.")
        c.execute(f"UPDATE areas SET {sets} WHERE id = ?", values)

    return {"id": area_id, **updates, "summary": "Area updated."}


def tool_save_spot(
    db: Any,
    google_client: Any,
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
    Save a spot. If place_id is provided, fetch details from Google.
    If place_id is None, name is required (manual spot).
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. Valid: {sorted(VALID_CATEGORIES)}"
        )

    with db.conn() as c:
        area = c.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()
    if not area:
        raise ValueError(f"Area '{area_id}' not found.")

    spot_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    if place_id:
        # Fetch from Google
        from .google import CapExceededError, parse_place_details
        try:
            raw = google_client.place_details_full(place_id)
        except CapExceededError as e:
            raise ValueError(str(e)) from e

        parsed = parse_place_details(raw)

        # Check if place_id already saved
        with db.conn() as c:
            existing = c.execute(
                "SELECT id FROM spots WHERE place_id = ?", (place_id,)
            ).fetchone()
            if existing:
                raise ValueError(
                    f"This place is already saved (spot id: {existing['id']})."
                )

        spot = {
            "id": spot_id,
            "place_id": place_id,
            "area_id": area_id,
            "name": parsed.get("name") or name or place_id,
            "category": category,
            "address": parsed.get("address"),
            "lat": parsed.get("lat"),
            "lng": parsed.get("lng"),
            "timezone": parsed.get("timezone"),
            "price_level": parsed.get("price_level"),
            "rating": parsed.get("rating"),
            "user_rating_count": parsed.get("user_rating_count"),
            "regular_hours_json": parsed.get("regular_hours_json"),
            "current_hours_json": parsed.get("current_hours_json"),
            "hours_fetched_at": parsed.get("hours_fetched_at"),
            "occasions_json": json.dumps(occasions or []),
            "tags_json": json.dumps(tags or []),
            "notes": notes,
            "saved_at": now_iso,
        }
    else:
        # Manual spot
        if not name:
            raise ValueError("'name' is required for manual spots (no place_id).")
        spot = {
            "id": spot_id,
            "place_id": None,
            "area_id": area_id,
            "name": name,
            "category": category,
            "address": address,
            "lat": lat,
            "lng": lng,
            "timezone": None,
            "price_level": None,
            "rating": None,
            "user_rating_count": None,
            "regular_hours_json": None,
            "current_hours_json": None,
            "hours_fetched_at": None,
            "occasions_json": json.dumps(occasions or []),
            "tags_json": json.dumps(tags or []),
            "notes": notes,
            "saved_at": now_iso,
        }

    with db.conn() as c:
        c.execute(
            """
            INSERT INTO spots (
                id, place_id, area_id, name, category, address, lat, lng,
                timezone, price_level, rating, user_rating_count,
                regular_hours_json, current_hours_json, hours_fetched_at,
                occasions_json, tags_json, notes, saved_at
            ) VALUES (
                :id, :place_id, :area_id, :name, :category, :address, :lat, :lng,
                :timezone, :price_level, :rating, :user_rating_count,
                :regular_hours_json, :current_hours_json, :hours_fetched_at,
                :occasions_json, :tags_json, :notes, :saved_at
            )
            """,
            spot,
        )

    return {
        **{k: v for k, v in spot.items() if k not in ("regular_hours_json", "current_hours_json")},
        "open_status": "unknown" if not place_id else "unknown",
        "summary": f"Saved '{spot['name']}' in {area['name']}.",
    }


def tool_list_spots(
    db: Any,
    google_client: Any,
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

    near: spot UUID, Google place_id, or "lat,lng"
    open_at: ISO-8601 datetime string or None for now
    """
    with db.conn() as c:
        # Build query
        where_parts = []
        params: list[Any] = []

        if area_id:
            where_parts.append("s.area_id = ?")
            params.append(area_id)

        if category:
            where_parts.append("s.category = ?")
            params.append(category)

        if min_rating is not None:
            where_parts.append("(s.rating IS NULL OR s.rating >= ?)")
            params.append(min_rating)

        if max_price_level is not None:
            where_parts.append("(s.price_level IS NULL OR s.price_level <= ?)")
            params.append(max_price_level)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = c.execute(
            f"""
            SELECT s.*, a.timezone AS area_timezone, a.name AS area_name,
                   a.center_lat, a.center_lng
            FROM spots s
            JOIN areas a ON s.area_id = a.id
            {where_sql}
            ORDER BY s.saved_at DESC
            """,
            params,
        ).fetchall()

    spots = [dict(r) for r in rows]

    # Filter by occasions (JSON array overlap)
    if occasions:
        filtered = []
        for s in spots:
            spot_occasions = json.loads(s.get("occasions_json") or "[]")
            if any(o in spot_occasions for o in occasions):
                filtered.append(s)
        spots = filtered

    # Filter by tags (JSON array overlap)
    if tags:
        filtered = []
        for s in spots:
            spot_tags = json.loads(s.get("tags_json") or "[]")
            if any(t in spot_tags for t in tags):
                filtered.append(s)
        spots = filtered

    # Resolve 'near' to (lat, lng)
    near_lat: float | None = None
    near_lng: float | None = None
    if near:
        near_lat, near_lng = _resolve_near(near, spots, db)

    # Filter by distance and add distance field
    if near_lat is not None and near_lng is not None:
        for s in spots:
            slat = s.get("lat")
            slng = s.get("lng")
            if slat is not None and slng is not None:
                s["distance_km"] = round(haversine_km(near_lat, near_lng, slat, slng), 2)
            else:
                s["distance_km"] = None

        if max_distance_km is not None:
            spots = [
                s for s in spots
                if s.get("distance_km") is None or s["distance_km"] <= max_distance_km
            ]

        # Sort by distance when near is specified
        spots.sort(key=lambda s: s.get("distance_km") or float("inf"))

    # Determine open_at datetime
    check_dt: datetime | None = None
    if open_at:
        try:
            check_dt = datetime.fromisoformat(open_at)
            if check_dt.tzinfo is None:
                check_dt = check_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning("Invalid open_at datetime: %s", open_at)

    # Refresh hours if needed and add open_status
    result_spots = []
    for s in spots:
        if s.get("place_id"):
            s = _maybe_refresh_hours(s, google_client, db)
        status = _open_status(s, check_dt)
        s["open_status"] = status["open_status"]
        s["open_status_reason"] = status["reason"]
        s["regular_hours_only"] = status.get("regular_hours_only", False)
        # Remove raw JSON blobs from output — keep it clean for Claude
        s.pop("regular_hours_json", None)
        s.pop("current_hours_json", None)
        result_spots.append(s)

    # Filter by open_at status if requested
    if open_at and check_dt is not None:
        # Only filter to open if the user clearly wants open places
        # (Don't silently exclude unknowns — return all with status annotated)
        pass

    return {
        "spots": result_spots,
        "count": len(result_spots),
        "summary": f"{len(result_spots)} spot(s) found.",
    }


def tool_check_hours(
    db: Any,
    google_client: Any,
    spot_id: str | None = None,
    place_id: str | None = None,
    at: str | None = None,
) -> dict:
    """
    Check if a spot is open at a given datetime.
    at: ISO-8601 datetime string, or None for now.
    Returns open_status: open | closed | unknown — never guesses.
    """
    if not spot_id and not place_id:
        raise ValueError("Either spot_id or place_id is required.")

    with db.conn() as c:
        if spot_id:
            row = c.execute(
                "SELECT s.*, a.timezone AS area_timezone FROM spots s "
                "JOIN areas a ON s.area_id = a.id WHERE s.id = ?",
                (spot_id,),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT s.*, a.timezone AS area_timezone FROM spots s "
                "JOIN areas a ON s.area_id = a.id WHERE s.place_id = ?",
                (place_id,),
            ).fetchone()

    if not row:
        raise ValueError(f"Spot not found.")

    spot = dict(row)
    spot = _maybe_refresh_hours(spot, google_client, db)

    check_dt: datetime | None = None
    if at:
        try:
            check_dt = datetime.fromisoformat(at)
            if check_dt.tzinfo is None:
                check_dt = check_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError(f"Invalid datetime format for 'at': {at!r}")

    result = _open_status(spot, check_dt)
    return {
        "spot_id": spot["id"],
        "name": spot["name"],
        "at": (check_dt or datetime.now(timezone.utc)).isoformat(),
        **result,
    }


def tool_log_visit(
    db: Any,
    spot_id: str,
    rating: int | None = None,
    notes: str | None = None,
    occasion: str | None = None,
    with_whom: str | None = None,
) -> dict:
    """Log a visit to a spot."""
    if rating is not None and rating not in range(1, 6):
        raise ValueError("Rating must be 1-5.")

    with db.conn() as c:
        row = c.execute("SELECT name FROM spots WHERE id = ?", (spot_id,)).fetchone()
        if not row:
            raise ValueError(f"Spot '{spot_id}' not found.")
        spot_name = row["name"]

        visit_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        c.execute(
            """
            INSERT INTO visits (id, spot_id, visited_at, rating, notes, occasion, with_whom)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (visit_id, spot_id, now_iso, rating, notes, occasion, with_whom),
        )

    return {
        "visit_id": visit_id,
        "spot_id": spot_id,
        "spot_name": spot_name,
        "visited_at": now_iso,
        "rating": rating,
        "summary": f"Visit to '{spot_name}' logged.",
    }


def tool_discover(
    db: Any,
    google_client: Any,
    area_id: str,
    query: str,
    occasion: str | None = None,
    max_results: int = 10,
) -> dict:
    """
    Search Google for places matching query in the given area.
    Returns candidates with an already_saved flag. Does not auto-save.
    """
    with db.conn() as c:
        area = c.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()
    if not area:
        raise ValueError(f"Area '{area_id}' not found.")

    area = dict(area)
    radius_m = area["radius_km"] * 1000

    from .google import CapExceededError
    try:
        raw_places = google_client.text_search(
            query=query,
            lat=area["center_lat"],
            lng=area["center_lng"],
            radius_m=radius_m,
            max_results=max_results,
        )
    except CapExceededError as e:
        raise ValueError(str(e)) from e

    # Look up which place_ids are already saved
    place_ids = [p.get("id") for p in raw_places if p.get("id")]
    saved_ids: set[str] = set()
    if place_ids:
        with db.conn() as c:
            placeholders = ",".join("?" * len(place_ids))
            rows = c.execute(
                f"SELECT place_id FROM spots WHERE place_id IN ({placeholders})",
                place_ids,
            ).fetchall()
        saved_ids = {r["place_id"] for r in rows}

    candidates = []
    for p in raw_places:
        pid = p.get("id")
        location = p.get("location", {})
        display = p.get("displayName", {})
        candidates.append({
            "place_id": pid,
            "name": display.get("text") or display.get("languageCode", ""),
            "address": p.get("formattedAddress"),
            "lat": location.get("latitude"),
            "lng": location.get("longitude"),
            "rating": p.get("rating"),
            "user_rating_count": p.get("userRatingCount"),
            "price_level": p.get("priceLevel"),
            "already_saved": pid in saved_ids if pid else False,
        })

    return {
        "area": area["name"],
        "query": query,
        "occasion": occasion,
        "candidates": candidates,
        "count": len(candidates),
        "summary": (
            f"{len(candidates)} result(s) for '{query}' in {area['name']}. "
            f"{sum(1 for c in candidates if c['already_saved'])} already saved."
        ),
    }


def tool_travel_time(
    db: Any,
    google_routes: Any,
    origin: str,
    destinations: list[str],
    mode: str = "DRIVE",
    depart_at: str | None = None,
) -> dict:
    """
    Get travel time from one origin to up to 5 destinations.

    origin / destinations: spot UUID, Google place_id, or 'lat,lng' string.
    mode: DRIVE | WALK | BICYCLE | TRANSIT
    depart_at: accepted for compatibility but ignored — Google requires traffic
               routing to use departure times, and spotbook never uses traffic
               routing (always Essentials SKU, no extra cost).

    Results are cached 24 hours. Cache hits cost nothing.
    Billing: Routes Essentials, 1 element per destination. Never Pro or Enterprise.
    """
    from .google import CapExceededError, _format_duration, _format_distance

    mode = mode.upper()
    if mode not in {"DRIVE", "WALK", "BICYCLE", "TRANSIT"}:
        raise ValueError(f"Invalid mode '{mode}'. Valid: DRIVE, WALK, BICYCLE, TRANSIT")

    if not destinations:
        raise ValueError("At least one destination is required.")
    if len(destinations) > 5:
        raise ValueError("Maximum 5 destinations per call.")

    # Resolve origin
    origin_lat, origin_lng = _resolve_to_latlng(origin, db)
    if origin_lat is None or origin_lng is None:
        raise ValueError(f"Could not resolve origin '{origin}' to coordinates.")

    # Resolve each destination
    resolved_dests: list[tuple[float, float]] = []
    dest_labels: list[str] = []
    for d in destinations:
        lat, lng = _resolve_to_latlng(d, db)
        if lat is None or lng is None:
            raise ValueError(f"Could not resolve destination '{d}' to coordinates.")
        resolved_dests.append((lat, lng))
        dest_labels.append(_dest_label(d, db))

    # Check cache before calling API so the flag is accurate
    cache_key = google_routes.cache_key(origin_lat, origin_lng, resolved_dests, mode)
    was_cached = google_routes._db.get_travel_time_cache(cache_key) is not None

    # Call Routes API (or return from cache)
    try:
        elements = google_routes.compute_route_matrix(
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lls=resolved_dests,
            mode=mode,
            depart_at=depart_at,
        )
    except CapExceededError as e:
        raise ValueError(str(e)) from e

    # Build results indexed by destinationIndex
    indexed: dict[int, dict] = {}
    for el in elements:
        idx = el.get("destinationIndex", 0)
        indexed[idx] = el

    results = []
    for i, label in enumerate(dest_labels):
        el = indexed.get(i, {})
        status = el.get("status", {})
        condition = el.get("condition", "ROUTE_EXISTS")
        ok = (
            (not status or status.get("code", 0) == 0)
            and condition == "ROUTE_EXISTS"
        )

        # duration comes as e.g. {"seconds": "720"}
        raw_dur = el.get("duration")
        dur_secs: int | None = None
        if raw_dur:
            try:
                # API returns duration as string "NNNs" or object {"seconds": "NNN"}
                if isinstance(raw_dur, str):
                    dur_secs = int(raw_dur.rstrip("s"))
                elif isinstance(raw_dur, dict):
                    dur_secs = int(raw_dur.get("seconds", 0))
            except (ValueError, TypeError):
                dur_secs = None

        dist_m = el.get("distanceMeters")

        results.append({
            "destination": label,
            "destination_index": i,
            "ok": ok,
            "duration_seconds": dur_secs,
            "duration_text": _format_duration(dur_secs) if ok else "unavailable",
            "distance_meters": dist_m,
            "distance_text": _format_distance(dist_m) if ok else "unavailable",
            "condition": condition,
            "status_code": status.get("code") if status else 0,
        })

    ok_count = sum(1 for r in results if r["ok"])
    summary = f"{ok_count}/{len(results)} route(s) found by {mode.lower()}"
    if was_cached:
        summary += " (cached)"
    summary += "."

    return {
        "origin": _dest_label(origin, db),
        "mode": mode,
        "traffic_aware": False,
        "note": "Durations are static (no traffic). depart_at is ignored.",
        "results": results,
        "cached": was_cached,
        "summary": summary,
    }


def tool_usage_status(
    db: Any,
    google_places: Any,
    google_routes: Any,
) -> dict:
    """
    Return current Google API usage by SKU — monthly count, cap, percentage,
    daily count, daily cap, and reset date. Useful before expensive operations
    or when asked how much quota remains.
    """
    from .google import (
        PLACES_SKU_ESSENTIALS, PLACES_SKU_PRO, PLACES_SKU_ENTERPRISE,
        ROUTES_SKU_ESSENTIALS, ROUTES_SKU_PRO, ROUTES_SKU_ENTERPRISE,
        _SKU_LABELS,
    )
    from .db import pacific_month, pacific_next_month_reset, utc_date_str

    month = pacific_month()
    today = utc_date_str()
    reset_date = pacific_next_month_reset()

    all_skus = [
        (PLACES_SKU_ESSENTIALS,  google_places._caps[PLACES_SKU_ESSENTIALS]),
        (PLACES_SKU_PRO,         google_places._caps[PLACES_SKU_PRO]),
        (PLACES_SKU_ENTERPRISE,  google_places._caps[PLACES_SKU_ENTERPRISE]),
        (ROUTES_SKU_ESSENTIALS,  google_routes._caps[ROUTES_SKU_ESSENTIALS]),
        (ROUTES_SKU_PRO,         google_routes._caps[ROUTES_SKU_PRO]),
        (ROUTES_SKU_ENTERPRISE,  google_routes._caps[ROUTES_SKU_ENTERPRISE]),
    ]

    daily_fraction = google_places._daily_cap_fraction
    skus_out = []
    any_warning = False

    for sku, monthly_cap in all_skus:
        monthly_count = db.get_api_usage(month, sku)
        daily_cap = max(1, int(monthly_cap * daily_fraction))
        daily_count = db.get_daily_usage(today, sku)
        monthly_pct = round(monthly_count / monthly_cap * 100, 1) if monthly_cap else 0
        daily_pct = round(daily_count / daily_cap * 100, 1) if daily_cap else 0
        warning = monthly_pct >= 80

        if warning:
            any_warning = True

        skus_out.append({
            "sku": sku,
            "label": _SKU_LABELS.get(sku, sku),
            "monthly_count": monthly_count,
            "monthly_cap": monthly_cap,
            "monthly_pct": monthly_pct,
            "daily_count": daily_count,
            "daily_cap": daily_cap,
            "daily_pct": daily_pct,
            "warning": warning,
        })

    summary = "All SKUs within normal limits." if not any_warning else \
        "⚠️ One or more SKUs are at 80%+ of their monthly cap."

    return {
        "month": month,
        "reset_date": reset_date,
        "skus": skus_out,
        "cloud_console_quotas": {
            "note": "Hard quotas set in Google Cloud Console as a second safety layer.",
            "Routes API - ComputeRouteMatrix per-element/day": 500,
            "Routes API - ComputeRouteMatrix per-element/min": 100,
            "Places API (New) - GetPlaceRequest per day": 30,
            "Places API (New) - SearchTextRequest per day": 30,
        },
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_near(
    near: str,
    spots: list[dict],
    db: Any,
) -> tuple[float | None, float | None]:
    """
    Resolve 'near' to (lat, lng).

    near can be:
      - "lat,lng" (e.g. "47.6740,-122.1215")
      - a spot UUID
      - a Google place_id
    """
    # Try "lat,lng" format first
    if "," in near:
        parts = near.split(",", 1)
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass

    # Try spot UUID from already-fetched spots list
    for s in spots:
        if s.get("id") == near:
            return s.get("lat"), s.get("lng")

    # Try looking up by place_id or UUID in the database
    with db.conn() as c:
        row = c.execute(
            "SELECT lat, lng FROM spots WHERE id = ? OR place_id = ?",
            (near, near),
        ).fetchone()
    if row:
        return row["lat"], row["lng"]

    log.warning("Could not resolve 'near' value: %s", near)
    return None, None


def _resolve_to_latlng(
    ref: str,
    db: Any,
) -> tuple[float | None, float | None]:
    """
    Resolve a spot UUID, Google place_id, or 'lat,lng' string to (lat, lng).
    Same as _resolve_near but without needing a pre-fetched spots list.
    """
    # Try "lat,lng" format first
    if "," in ref:
        parts = ref.split(",", 1)
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass

    # Look up by spot UUID or place_id in the database
    with db.conn() as c:
        row = c.execute(
            "SELECT lat, lng FROM spots WHERE id = ? OR place_id = ?",
            (ref, ref),
        ).fetchone()
    if row:
        return row["lat"], row["lng"]

    log.warning("Could not resolve ref to lat/lng: %s", ref)
    return None, None


def _dest_label(ref: str, db: Any) -> str:
    """Return a human-readable label for a spot reference."""
    if "," in ref:
        return ref  # raw lat,lng — use as-is
    with db.conn() as c:
        row = c.execute(
            "SELECT name FROM spots WHERE id = ? OR place_id = ?",
            (ref, ref),
        ).fetchone()
    return row["name"] if row else ref
