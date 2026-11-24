"""
hours.py — Pure functions for checking open/closed status from Google period data.

No I/O, no database, no external calls. All inputs are plain Python values so
this module is easy to unit-test in isolation.

Google period format (from regularOpeningHours / currentOpeningHours):
  {
    "open":  {"day": 0-6, "hour": 0-23, "minute": 0-59},
    "close": {"day": 0-6, "hour": 0-23, "minute": 0-59}  # absent for 24h
  }
  day 0 = Sunday, 1 = Monday, ... 6 = Saturday (Google convention)

check_hours() returns a dict:
  {
    "open_status": "open" | "closed" | "unknown",
    "regular_hours_only": bool,   # True when query is > 7 days out
    "reason": str                 # human-readable explanation for Claude
  }
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_hours(
    *,
    query_dt: datetime,
    area_timezone: str,
    spot_timezone: str | None,
    regular_hours_json: str | None,
    current_hours_json: str | None,
    hours_fetched_at: str | None,
) -> dict[str, Any]:
    """
    Determine open/closed status for a spot at the given datetime.

    Args:
        query_dt: The datetime to check. If naive, it is interpreted as UTC.
                  Callers should pass timezone-aware datetimes when possible.
        area_timezone: IANA timezone of the area (fallback if spot_timezone is None).
        spot_timezone: IANA timezone of the spot (from Google), may be None.
        regular_hours_json: JSON string of Google regularOpeningHours periods.
        current_hours_json: JSON string of Google currentOpeningHours periods.
        hours_fetched_at: ISO-8601 UTC string of when hours were last fetched.

    Returns:
        dict with keys: open_status, regular_hours_only, reason
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    # --- Determine effective timezone ---
    tz_name = spot_timezone or area_timezone
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return {
            "open_status": "unknown",
            "regular_hours_only": False,
            "reason": f"Unknown timezone '{tz_name}'",
        }

    # --- Make query_dt timezone-aware in spot's zone ---
    if query_dt.tzinfo is None:
        query_dt = query_dt.replace(tzinfo=timezone.utc)
    local_dt = query_dt.astimezone(tz)

    # --- No hours at all ---
    if not regular_hours_json and not current_hours_json:
        return {
            "open_status": "unknown",
            "regular_hours_only": False,
            "reason": "No hours data stored for this spot.",
        }

    # --- Decide which periods to use ---
    # currentOpeningHours covers ~7 days from the fetch date and includes
    # holiday exceptions. Use it when the query falls within that window.
    regular_hours_only = True
    periods = None

    fetched_at_dt: datetime | None = None
    if hours_fetched_at:
        try:
            fetched_at_dt = datetime.fromisoformat(hours_fetched_at)
            if fetched_at_dt.tzinfo is None:
                fetched_at_dt = fetched_at_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            fetched_at_dt = None

    if current_hours_json and fetched_at_dt is not None:
        window_end = fetched_at_dt + timedelta(days=7)
        if query_dt <= window_end:
            try:
                current_periods = json.loads(current_hours_json)
                if isinstance(current_periods, list) and current_periods:
                    periods = current_periods
                    regular_hours_only = False
            except (json.JSONDecodeError, TypeError):
                pass

    if periods is None and regular_hours_json:
        try:
            regular_periods = json.loads(regular_hours_json)
            if isinstance(regular_periods, list):
                periods = regular_periods
        except (json.JSONDecodeError, TypeError):
            pass

    if not periods:
        return {
            "open_status": "unknown",
            "regular_hours_only": regular_hours_only,
            "reason": "Hours data is empty or malformed.",
        }

    # --- Evaluate periods ---
    result = _is_open(local_dt, periods)
    return {
        "open_status": result,
        "regular_hours_only": regular_hours_only,
        "reason": _status_reason(result, local_dt, regular_hours_only),
    }


# ---------------------------------------------------------------------------
# Internal logic
# ---------------------------------------------------------------------------

def _is_open(local_dt: datetime, periods: list[dict]) -> str:
    """
    Return 'open', 'closed', or 'unknown' by evaluating Google period data.

    Google day numbers: 0=Sunday, 1=Monday, ..., 6=Saturday.
    Python weekday(): 0=Monday, ..., 6=Sunday.
    Convert: google_day = (python_weekday + 1) % 7
    """
    if not periods:
        return "unknown"

    # Check for 24-hours open: single period, open day=0 00:00, no close key
    if len(periods) == 1:
        p = periods[0]
        o = p.get("open", {})
        if (
            o.get("day") == 0
            and o.get("hour") == 0
            and o.get("minute") == 0
            and "close" not in p
        ):
            return "open"

    google_today = _python_to_google_day(local_dt.weekday())
    now_minutes = local_dt.hour * 60 + local_dt.minute

    for period in periods:
        open_pt = period.get("open")
        close_pt = period.get("close")

        if open_pt is None:
            continue

        open_day = open_pt.get("day")
        open_min = open_pt.get("hour", 0) * 60 + open_pt.get("minute", 0)

        if close_pt is None:
            # No close = open 24h (should have been caught above, but handle
            # multi-period edge case gracefully)
            if open_day == google_today and open_min <= now_minutes:
                return "open"
            continue

        close_day = close_pt.get("day")
        close_min = close_pt.get("hour", 0) * 60 + close_pt.get("minute", 0)

        if _in_period(
            google_today, now_minutes, open_day, open_min, close_day, close_min
        ):
            return "open"

    return "closed"


def _in_period(
    today: int,
    now_min: int,
    open_day: int,
    open_min: int,
    close_day: int,
    close_min: int,
) -> bool:
    """
    Return True if (today, now_min) falls within [open_day:open_min, close_day:close_min).

    Handles overnight periods (open_day != close_day) and same-day periods.
    All day values are Google day numbers (0=Sunday).
    """
    # Encode as (day * 1440 + minute) for linear comparison
    # Use a week-long timeline (0..10079) to handle wraparound naturally.
    # We check two windows: the period as-is, and the period shifted back one week
    # (so an overnight that opened yesterday is correctly detected as open now).

    MINS_PER_WEEK = 7 * 1440

    now_abs = today * 1440 + now_min
    open_abs = open_day * 1440 + open_min
    close_abs = close_day * 1440 + close_min

    # Overnight close: close_abs < open_abs on the absolute timeline means the
    # period wraps into the next week. Add a week to close_abs.
    if close_abs <= open_abs:
        close_abs += MINS_PER_WEEK

    # Check current window
    if open_abs <= now_abs < close_abs:
        return True

    # Check if we're inside a period that opened yesterday (shift back one week)
    open_abs_prev = open_abs - MINS_PER_WEEK
    close_abs_prev = close_abs - MINS_PER_WEEK
    if open_abs_prev <= now_abs < close_abs_prev:
        return True

    return False


def _python_to_google_day(weekday: int) -> int:
    """Convert Python weekday (0=Monday) to Google day (0=Sunday)."""
    return (weekday + 1) % 7


def _status_reason(status: str, local_dt: datetime, regular_hours_only: bool) -> str:
    # %-d and %-I are Linux-only; use lstrip("0") for cross-platform compatibility
    day_num = local_dt.strftime("%d").lstrip("0") or "0"
    hour_num = local_dt.strftime("%I").lstrip("0") or "12"
    dt_str = local_dt.strftime(f"%A %b {day_num} at {hour_num}:%M %p %Z")
    if status == "open":
        base = f"Open on {dt_str}"
    elif status == "closed":
        base = f"Closed on {dt_str}"
    else:
        base = f"Hours unknown for {dt_str}"

    if regular_hours_only:
        base += " (regular hours only — holiday exceptions not checked for this date)"

    return base
