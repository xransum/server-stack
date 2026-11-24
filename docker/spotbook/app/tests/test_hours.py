"""
test_hours.py — Unit tests for hours.py check_hours logic.

Run with: python -m pytest tests/test_hours.py -v

All tests use real zoneinfo timezones (requires tzdata package).
No mocking — the functions are pure and timezone-aware.
"""

import json
from datetime import datetime, timezone, timedelta

import pytest

from spotbook.hours import check_hours, _is_open, _in_period, _python_to_google_day


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_period(
    open_day: int, open_hour: int, open_min: int,
    close_day: int | None = None, close_hour: int | None = None,
    close_min: int | None = None,
) -> dict:
    p: dict = {"open": {"day": open_day, "hour": open_hour, "minute": open_min}}
    if close_day is not None:
        p["close"] = {"day": close_day, "hour": close_hour or 0, "minute": close_min or 0}
    return p


def dt_et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return a timezone-aware datetime in US/Eastern."""
    from zoneinfo import ZoneInfo
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def dt_pt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return a timezone-aware datetime in US/Pacific."""
    from zoneinfo import ZoneInfo
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/Los_Angeles"))


def dt_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


FETCHED_NOW = dt_utc(2026, 11, 23, 12, 0).isoformat()
FETCHED_OLD = dt_utc(2026, 11, 1, 12, 0).isoformat()  # > 7 days ago


def run_check(
    query_dt: datetime,
    regular_periods: list[dict] | None,
    current_periods: list[dict] | None = None,
    fetched_at: str | None = None,
    spot_tz: str | None = None,
    area_tz: str = "America/New_York",
) -> dict:
    return check_hours(
        query_dt=query_dt,
        area_timezone=area_tz,
        spot_timezone=spot_tz,
        regular_hours_json=json.dumps(regular_periods) if regular_periods is not None else None,
        current_hours_json=json.dumps(current_periods) if current_periods is not None else None,
        hours_fetched_at=fetched_at or FETCHED_NOW,
    )


# ---------------------------------------------------------------------------
# _python_to_google_day
# ---------------------------------------------------------------------------

def test_python_to_google_day_monday():
    assert _python_to_google_day(0) == 1  # Monday

def test_python_to_google_day_sunday():
    assert _python_to_google_day(6) == 0  # Sunday

def test_python_to_google_day_saturday():
    assert _python_to_google_day(5) == 6  # Saturday


# ---------------------------------------------------------------------------
# _in_period — same-day periods
# ---------------------------------------------------------------------------

def test_in_period_same_day_inside():
    # Monday 12pm, period Mon 10am-6pm
    assert _in_period(1, 12*60, 1, 10*60, 1, 18*60) is True

def test_in_period_same_day_before():
    assert _in_period(1, 9*60, 1, 10*60, 1, 18*60) is False

def test_in_period_same_day_after():
    assert _in_period(1, 19*60, 1, 10*60, 1, 18*60) is False

def test_in_period_same_day_at_open():
    assert _in_period(1, 10*60, 1, 10*60, 1, 18*60) is True

def test_in_period_same_day_at_close_exclusive():
    # Close is exclusive
    assert _in_period(1, 18*60, 1, 10*60, 1, 18*60) is False


# ---------------------------------------------------------------------------
# _in_period — overnight periods
# ---------------------------------------------------------------------------

def test_in_period_overnight_before_midnight():
    # Fri 5pm to Sat 2am: check Fri 10pm → open
    assert _in_period(5, 22*60, 5, 17*60, 6, 2*60) is True

def test_in_period_overnight_after_midnight():
    # Fri 5pm to Sat 2am: check Sat 1am → open
    assert _in_period(6, 1*60, 5, 17*60, 6, 2*60) is True

def test_in_period_overnight_after_close():
    # Fri 5pm to Sat 2am: check Sat 3am → closed
    assert _in_period(6, 3*60, 5, 17*60, 6, 2*60) is False

def test_in_period_overnight_before_open():
    # Fri 5pm to Sat 2am: check Fri 4pm → closed
    assert _in_period(5, 16*60, 5, 17*60, 6, 2*60) is False


# ---------------------------------------------------------------------------
# check_hours — no hours listed → unknown
# ---------------------------------------------------------------------------

def test_no_hours_returns_unknown():
    result = run_check(
        query_dt=dt_et(2026, 11, 24, 12),
        regular_periods=None,
        current_periods=None,
    )
    assert result["open_status"] == "unknown"
    assert result["regular_hours_only"] is False


# ---------------------------------------------------------------------------
# check_hours — 24-hour open
# ---------------------------------------------------------------------------

def test_24h_open():
    # Google 24h: single period, open day=0 00:00, no close
    periods = [{"open": {"day": 0, "hour": 0, "minute": 0}}]
    result = run_check(
        query_dt=dt_et(2026, 11, 24, 3, 30),  # 3:30am Tuesday
        regular_periods=periods,
    )
    assert result["open_status"] == "open"


# ---------------------------------------------------------------------------
# check_hours — closed day
# ---------------------------------------------------------------------------

def test_closed_day():
    # Restaurant open Mon-Fri 11am-9pm, closed Sat-Sun
    # Check Sunday → closed
    # 2026-11-29 is a Sunday
    periods = [
        make_period(1, 11, 0, 1, 21, 0),  # Mon
        make_period(2, 11, 0, 2, 21, 0),  # Tue
        make_period(3, 11, 0, 3, 21, 0),  # Wed
        make_period(4, 11, 0, 4, 21, 0),  # Thu
        make_period(5, 11, 0, 5, 21, 0),  # Fri
    ]
    result = run_check(
        query_dt=dt_et(2026, 11, 29, 14),  # Sunday 2pm
        regular_periods=periods,
    )
    assert result["open_status"] == "closed"


# ---------------------------------------------------------------------------
# check_hours — standard open/closed within hours
# ---------------------------------------------------------------------------

def test_open_within_hours():
    # Mon-Sun 9am-10pm, check Monday 2pm
    # 2026-11-23 is a Monday
    periods = [make_period(d, 9, 0, d, 22, 0) for d in range(7)]
    result = run_check(
        query_dt=dt_et(2026, 11, 23, 14),  # Monday 2pm ET
        regular_periods=periods,
    )
    assert result["open_status"] == "open"


def test_closed_outside_hours():
    periods = [make_period(d, 9, 0, d, 22, 0) for d in range(7)]
    result = run_check(
        query_dt=dt_et(2026, 11, 23, 23),  # Monday 11pm ET
        regular_periods=periods,
    )
    assert result["open_status"] == "closed"


# ---------------------------------------------------------------------------
# check_hours — overnight close (5pm to 2am)
# ---------------------------------------------------------------------------

def test_overnight_open_before_midnight():
    # Fri 5pm (day 5) to Sat 2am (day 6)
    # 2026-11-27 is Friday
    periods = [make_period(5, 17, 0, 6, 2, 0)]
    result = run_check(
        query_dt=dt_et(2026, 11, 27, 22),  # Friday 10pm
        regular_periods=periods,
    )
    assert result["open_status"] == "open"


def test_overnight_open_after_midnight():
    # 2026-11-28 is Saturday
    periods = [make_period(5, 17, 0, 6, 2, 0)]
    result = run_check(
        query_dt=dt_et(2026, 11, 28, 1),  # Saturday 1am
        regular_periods=periods,
    )
    assert result["open_status"] == "open"


def test_overnight_closed_after_close_time():
    periods = [make_period(5, 17, 0, 6, 2, 0)]
    result = run_check(
        query_dt=dt_et(2026, 11, 28, 3),  # Saturday 3am (past 2am close)
        regular_periods=periods,
    )
    assert result["open_status"] == "closed"


# ---------------------------------------------------------------------------
# check_hours — ET vs PT timezone interpretation
# ---------------------------------------------------------------------------

def test_et_vs_pt_same_utc_different_local():
    """
    A place in PT zone should evaluate against local PT time, not ET.
    Mon-Sun 9am-5pm local.
    UTC 20:00 = ET 15:00 (3pm, open) = PT 12:00 (noon, open).
    UTC 02:00 = ET 21:00 (9pm, closed) = PT 18:00 (6pm, closed).
    """
    periods = [make_period(d, 9, 0, d, 17, 0) for d in range(7)]

    # UTC 20:00 Monday = ET 3pm (open) = PT noon (open)
    q = dt_utc(2026, 11, 23, 20)
    result_et = run_check(q, regular_periods=periods, area_tz="America/New_York")
    result_pt = run_check(q, regular_periods=periods, area_tz="America/Los_Angeles")
    assert result_et["open_status"] == "open"
    assert result_pt["open_status"] == "open"

    # UTC 23:30 Monday = ET 6:30pm (closed after 5pm) = PT 3:30pm (open)
    q2 = dt_utc(2026, 11, 23, 23, 30)
    result_et2 = run_check(q2, regular_periods=periods, area_tz="America/New_York")
    result_pt2 = run_check(q2, regular_periods=periods, area_tz="America/Los_Angeles")
    assert result_et2["open_status"] == "closed"
    assert result_pt2["open_status"] == "open"


# ---------------------------------------------------------------------------
# check_hours — DST change weekend
# ---------------------------------------------------------------------------

def test_dst_spring_forward_et():
    """
    US Eastern spring forward: 2026-03-08 02:00 → 03:00.
    At 02:30 ET the clock jumps to 03:30. A period 10am-10pm should still be
    open at what becomes 3:30am (outside hours = closed), and open at noon.
    """
    periods = [make_period(d, 10, 0, d, 22, 0) for d in range(7)]

    # 2026-03-08 is Sunday (google day 0)
    # At UTC 07:00, ET clock is at 02:00 (about to spring); wall time is ambiguous.
    # At UTC 16:00, ET = 12:00 EDT (noon) — open
    q_noon = dt_utc(2026, 3, 8, 16)
    result = run_check(q_noon, regular_periods=periods, area_tz="America/New_York")
    assert result["open_status"] == "open"

    # At UTC 03:00, ET = 22:00 EST (10pm exactly) = closed (exclusive)
    q_close = dt_utc(2026, 3, 8, 3, 0)
    result2 = run_check(q_close, regular_periods=periods, area_tz="America/New_York")
    assert result2["open_status"] == "closed"


def test_dst_fall_back_et():
    """
    US Eastern fall back: 2026-11-01 02:00 → 01:00.
    At UTC 06:00, ET = 01:00 (clocks fall back — this hour repeats).
    A period Mon 10am-10pm should be closed at 1am.
    """
    # 2026-11-01 is Sunday (google day 0)
    periods = [make_period(d, 10, 0, d, 22, 0) for d in range(7)]

    q = dt_utc(2026, 11, 1, 6)  # ET 01:00 (fall back hour)
    result = run_check(q, regular_periods=periods, area_tz="America/New_York")
    assert result["open_status"] == "closed"


# ---------------------------------------------------------------------------
# check_hours — week-out date uses regular hours + caveat flag
# ---------------------------------------------------------------------------

def test_week_out_uses_regular_hours_with_caveat():
    """
    Query date is > 7 days after hours_fetched_at.
    Should use regular hours and set regular_hours_only=True.
    """
    periods = [make_period(d, 10, 0, d, 22, 0) for d in range(7)]
    current_periods = [make_period(d, 10, 0, d, 22, 0) for d in range(7)]  # same

    # Fetched 10 days ago → query is outside the 7-day currentOpeningHours window
    fetched = dt_utc(2026, 11, 13, 12).isoformat()
    q = dt_et(2026, 11, 23, 14)  # Monday 2pm, 10 days after fetch

    result = run_check(
        query_dt=q,
        regular_periods=periods,
        current_periods=current_periods,
        fetched_at=fetched,
    )
    assert result["open_status"] == "open"
    assert result["regular_hours_only"] is True
    assert "regular hours only" in result["reason"].lower()


def test_within_7_days_uses_current_hours():
    """Query within 7 days of fetch uses currentOpeningHours (regular_hours_only=False)."""
    # Regular hours say closed, current hours say open (exception day)
    regular = [make_period(1, 10, 0, 1, 22, 0)]  # only Mon
    # Current hours include Sunday as well
    current = [make_period(d, 10, 0, d, 22, 0) for d in range(7)]

    fetched = dt_utc(2026, 11, 23, 12).isoformat()
    q = dt_et(2026, 11, 29, 14)  # Sunday 2pm, 6 days after fetch

    result = run_check(
        query_dt=q,
        regular_periods=regular,
        current_periods=current,
        fetched_at=fetched,
    )
    assert result["open_status"] == "open"
    assert result["regular_hours_only"] is False


# ---------------------------------------------------------------------------
# check_hours — multiple periods per day
# ---------------------------------------------------------------------------

def test_multiple_periods_per_day_first_slot():
    # Lunch 11am-2pm, Dinner 5pm-10pm on Monday
    periods = [
        make_period(1, 11, 0, 1, 14, 0),
        make_period(1, 17, 0, 1, 22, 0),
    ]
    # 2026-11-23 is Monday
    result = run_check(dt_et(2026, 11, 23, 12), regular_periods=periods)
    assert result["open_status"] == "open"


def test_multiple_periods_per_day_between_slots():
    periods = [
        make_period(1, 11, 0, 1, 14, 0),
        make_period(1, 17, 0, 1, 22, 0),
    ]
    result = run_check(dt_et(2026, 11, 23, 15), regular_periods=periods)
    assert result["open_status"] == "closed"


def test_multiple_periods_per_day_second_slot():
    periods = [
        make_period(1, 11, 0, 1, 14, 0),
        make_period(1, 17, 0, 1, 22, 0),
    ]
    result = run_check(dt_et(2026, 11, 23, 19), regular_periods=periods)
    assert result["open_status"] == "open"


# ---------------------------------------------------------------------------
# check_hours — spot_timezone overrides area_timezone
# ---------------------------------------------------------------------------

def test_spot_timezone_overrides_area():
    """A PT spot in a Manassas (ET) area should use PT for evaluation."""
    periods = [make_period(d, 9, 0, d, 17, 0) for d in range(7)]

    # UTC 22:00 Mon = ET 5pm (closed, 17:00 exclusive) = PT 2pm (open)
    q = dt_utc(2026, 11, 23, 22)
    result = run_check(
        q,
        regular_periods=periods,
        spot_tz="America/Los_Angeles",
        area_tz="America/New_York",
    )
    assert result["open_status"] == "open"


# ---------------------------------------------------------------------------
# check_hours — empty / malformed hours
# ---------------------------------------------------------------------------

def test_empty_periods_list():
    result = run_check(dt_et(2026, 11, 23, 14), regular_periods=[])
    assert result["open_status"] == "unknown"


def test_malformed_json():
    result = check_hours(
        query_dt=dt_et(2026, 11, 23, 14),
        area_timezone="America/New_York",
        spot_timezone=None,
        regular_hours_json="not valid json {{{",
        current_hours_json=None,
        hours_fetched_at=FETCHED_NOW,
    )
    assert result["open_status"] == "unknown"
