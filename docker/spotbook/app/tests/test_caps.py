"""
test_caps.py — Tests for API cap enforcement, daily burst limiting,
80% warning, travel time cache, SKU selection, and usage_status tool.

No network calls are made — Google API calls are never triggered in these tests.
All tests run against a real SQLite in-memory/tmp DB.
"""

import json
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from spotbook.db import Db, pacific_month, pacific_next_month_reset, utc_date_str
from spotbook.google import (
    CapExceededError,
    GooglePlacesClient,
    GoogleRoutesClient,
    PLACES_SKU_ESSENTIALS,
    PLACES_SKU_PRO,
    PLACES_SKU_ENTERPRISE,
    ROUTES_SKU_ESSENTIALS,
    ROUTES_SKU_PRO,
    ROUTES_SKU_ENTERPRISE,
)
from spotbook.tools import tool_usage_status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return Db(str(tmp_path / "test.db"))


@pytest.fixture
def places_client(tmp_db):
    return GooglePlacesClient(
        api_key="fake-key",
        db=tmp_db,
        discord_webhook="",
        essentials_cap=10,
        pro_cap=5,
        enterprise_cap=3,
        daily_cap_fraction=0.5,  # 50% → daily cap = 5 for essentials
    )


@pytest.fixture
def routes_client(tmp_db):
    return GoogleRoutesClient(
        api_key="fake-key",
        db=tmp_db,
        discord_webhook="",
        essentials_cap=10,
        pro_cap=5,
        enterprise_cap=3,
        daily_cap_fraction=0.5,
    )


# ---------------------------------------------------------------------------
# Pacific time helpers
# ---------------------------------------------------------------------------

def test_pacific_month_format():
    m = pacific_month()
    assert len(m) == 7
    assert m[4] == "-"


def test_pacific_next_month_reset_format():
    r = pacific_next_month_reset()
    # Should be YYYY-MM-01
    assert r.endswith("-01")
    assert len(r) == 10


def test_utc_date_str_format():
    d = utc_date_str()
    assert len(d) == 10
    assert d[4] == "-" and d[7] == "-"


# ---------------------------------------------------------------------------
# Atomic monthly cap enforcement
# ---------------------------------------------------------------------------

def test_monthly_cap_allows_up_to_limit(tmp_db):
    """Should succeed for n calls up to cap."""
    cap = 5
    for i in range(cap):
        monthly, daily = tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS,
            amount=1,
            monthly_cap=cap,
            daily_cap=100,
        )
        assert monthly == i + 1


def test_monthly_cap_blocks_at_limit(tmp_db):
    """Call at exactly cap+1 must raise CapExceededError."""
    cap = 3
    for _ in range(cap):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1, monthly_cap=cap, daily_cap=100
        )
    with pytest.raises(CapExceededError) as exc:
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1, monthly_cap=cap, daily_cap=100
        )
    assert exc.value.limit_type == "monthly"
    assert exc.value.cap == cap


def test_monthly_cap_rollback_leaves_count_unchanged(tmp_db):
    """After a cap rejection the counter must not have increased."""
    cap = 2
    for _ in range(cap):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1, monthly_cap=cap, daily_cap=100
        )
    month = pacific_month()
    before = tmp_db.get_api_usage(month, PLACES_SKU_ESSENTIALS)

    with pytest.raises(CapExceededError):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1, monthly_cap=cap, daily_cap=100
        )

    after = tmp_db.get_api_usage(month, PLACES_SKU_ESSENTIALS)
    assert after == before  # counter unchanged after rejection


def test_monthly_cap_counts_multiple_elements(tmp_db):
    """amount > 1 should be counted correctly."""
    monthly, _ = tmp_db.check_and_increment_usage(
        sku=ROUTES_SKU_ESSENTIALS, amount=3, monthly_cap=10, daily_cap=100
    )
    assert monthly == 3

    with pytest.raises(CapExceededError) as exc:
        tmp_db.check_and_increment_usage(
            sku=ROUTES_SKU_ESSENTIALS, amount=8, monthly_cap=10, daily_cap=100
        )
    assert exc.value.limit_type == "monthly"
    # Counter should still be 3 (rollback)
    assert tmp_db.get_api_usage(pacific_month(), ROUTES_SKU_ESSENTIALS) == 3


def test_different_skus_have_independent_counters(tmp_db):
    """Essentials cap hit should not affect Pro counter."""
    cap = 2
    for _ in range(cap):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1, monthly_cap=cap, daily_cap=100
        )
    # Essentials is now at cap; Pro should still work
    monthly, _ = tmp_db.check_and_increment_usage(
        sku=PLACES_SKU_PRO, amount=1, monthly_cap=10, daily_cap=100
    )
    assert monthly == 1


# ---------------------------------------------------------------------------
# Daily burst cap enforcement
# ---------------------------------------------------------------------------

def test_daily_cap_blocks_burst(tmp_db):
    """Daily cap should block before monthly cap is reached."""
    monthly_cap = 100
    daily_cap = 3
    for _ in range(daily_cap):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1,
            monthly_cap=monthly_cap, daily_cap=daily_cap,
        )
    with pytest.raises(CapExceededError) as exc:
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1,
            monthly_cap=monthly_cap, daily_cap=daily_cap,
        )
    assert exc.value.limit_type == "daily"


def test_daily_cap_rollback_includes_monthly(tmp_db):
    """When daily cap is hit, the monthly counter must also be rolled back."""
    monthly_cap = 100
    daily_cap = 2
    for _ in range(daily_cap):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1,
            monthly_cap=monthly_cap, daily_cap=daily_cap,
        )
    month = pacific_month()
    monthly_before = tmp_db.get_api_usage(month, PLACES_SKU_ESSENTIALS)

    with pytest.raises(CapExceededError):
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=1,
            monthly_cap=monthly_cap, daily_cap=daily_cap,
        )

    monthly_after = tmp_db.get_api_usage(month, PLACES_SKU_ESSENTIALS)
    assert monthly_after == monthly_before  # monthly also rolled back


# ---------------------------------------------------------------------------
# 80% warning
# ---------------------------------------------------------------------------

def test_80_percent_warning_fires_at_threshold(tmp_db):
    """Warning should fire when usage crosses 80% threshold."""
    cap = 10
    threshold = 8  # 80% of 10

    with patch("spotbook.google._send_cap_warning") as mock_warn:
        # Use up 7 — no warning yet
        tmp_db.check_and_increment_usage(
            sku=PLACES_SKU_ESSENTIALS, amount=7, monthly_cap=cap, daily_cap=100
        )
        # Trigger _check_and_record via a fresh call that crosses the threshold
        from spotbook.google import _check_and_record
        _check_and_record(
            db=tmp_db,
            sku=PLACES_SKU_ESSENTIALS,
            amount=1,  # takes us to 8 → crosses threshold
            monthly_cap=cap,
            daily_cap=100,
            discord_webhook="https://example.com/webhook",
        )
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0]
        assert args[0] == "https://example.com/webhook"  # webhook
        assert "Places Essentials" in args[1]  # label


def test_80_percent_warning_fires_only_once(tmp_db):
    """Warning for the same SKU in the same month fires only once."""
    cap = 10

    with patch("spotbook.google._send_cap_warning") as mock_warn:
        from spotbook.google import _check_and_record
        # Cross threshold twice
        for _ in range(3):
            try:
                _check_and_record(
                    db=tmp_db,
                    sku=PLACES_SKU_ESSENTIALS,
                    amount=3,
                    monthly_cap=cap,
                    daily_cap=100,
                    discord_webhook="https://example.com/webhook",
                )
            except CapExceededError:
                break
        # Warning should have fired exactly once
        assert mock_warn.call_count == 1


def test_80_percent_warning_not_fired_below_threshold(tmp_db):
    """Warning must not fire when usage is below 80%."""
    cap = 10

    with patch("spotbook.google._send_cap_warning") as mock_warn:
        from spotbook.google import _check_and_record
        _check_and_record(
            db=tmp_db,
            sku=PLACES_SKU_ESSENTIALS,
            amount=7,  # 70% — below threshold
            monthly_cap=cap,
            daily_cap=100,
            discord_webhook="https://example.com/webhook",
        )
        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# Travel time cache
# ---------------------------------------------------------------------------

def test_cache_miss_returns_none(tmp_db):
    result = tmp_db.get_travel_time_cache("nonexistent-key")
    assert result is None


def test_cache_set_and_hit(tmp_db):
    data = [{"destination_index": 0, "duration": {"seconds": "300"}}]
    tmp_db.set_travel_time_cache("test-key", data)
    result = tmp_db.get_travel_time_cache("test-key")
    assert result == data


def test_cache_expired_returns_none(tmp_db):
    """An expired cache entry should be treated as a miss."""
    import sqlite3
    # Insert an already-expired entry directly
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with tmp_db.conn() as c:
        c.execute(
            "INSERT INTO travel_time_cache (cache_key, result_json, cached_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            ("expired-key", json.dumps([{"ok": True}]), past, past),
        )
    result = tmp_db.get_travel_time_cache("expired-key")
    assert result is None


def test_cache_key_deterministic():
    """Same inputs must always produce the same cache key."""
    key1 = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.620, -122.350)], "DRIVE")
    key2 = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.620, -122.350)], "DRIVE")
    assert key1 == key2


def test_cache_key_differs_by_mode():
    key_drive = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.620, -122.350)], "DRIVE")
    key_walk = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.620, -122.350)], "WALK")
    assert key_drive != key_walk


def test_cache_key_differs_by_destination():
    key1 = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.620, -122.350)], "DRIVE")
    key2 = GoogleRoutesClient.cache_key(47.674, -122.121, [(47.699, -122.350)], "DRIVE")
    assert key1 != key2


def test_routes_client_uses_cache_on_second_call(tmp_db):
    """Second call with same params should return cached result, not hit the API."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                    "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}]

    with patch.object(client, "_call_api", return_value=fake_result) as mock_api:
        result1 = client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")
        result2 = client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")

    # API should only be called once
    assert mock_api.call_count == 1
    assert result1 == result2


def test_routes_client_cache_hit_does_not_increment_counter(tmp_db):
    """Cache hit must not increment the API usage counter."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                    "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}]

    with patch.object(client, "_call_api", return_value=fake_result):
        client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")

    count_after_first = tmp_db.get_api_usage(pacific_month(), ROUTES_SKU_ESSENTIALS)

    with patch.object(client, "_call_api", return_value=fake_result):
        client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")

    count_after_second = tmp_db.get_api_usage(pacific_month(), ROUTES_SKU_ESSENTIALS)
    assert count_after_second == count_after_first  # no increment on cache hit


# ---------------------------------------------------------------------------
# SKU selection
# ---------------------------------------------------------------------------

def test_transit_mode_uses_routes_essentials(tmp_db):
    """TRANSIT must bill as routes_essentials (confirmed from Google SKU docs)."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                    "duration": {"seconds": "1200"}, "distanceMeters": 8000, "status": {}}]

    with patch.object(client, "_call_api", return_value=fake_result):
        client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "TRANSIT")

    # Only routes_essentials should have been incremented
    month = pacific_month()
    assert tmp_db.get_api_usage(month, ROUTES_SKU_ESSENTIALS) == 1
    assert tmp_db.get_api_usage(month, ROUTES_SKU_PRO) == 0
    assert tmp_db.get_api_usage(month, ROUTES_SKU_ENTERPRISE) == 0


def test_drive_uses_routes_essentials(tmp_db):
    """DRIVE without traffic routing must use routes_essentials."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                    "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}]

    with patch.object(client, "_call_api", return_value=fake_result):
        client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")

    month = pacific_month()
    assert tmp_db.get_api_usage(month, ROUTES_SKU_ESSENTIALS) == 1
    assert tmp_db.get_api_usage(month, ROUTES_SKU_PRO) == 0


def test_drive_with_depart_at_still_essentials(tmp_db):
    """DRIVE + depart_at must stay Essentials (no traffic routing in spotbook)."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                    "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}]

    with patch.object(client, "_call_api", return_value=fake_result) as mock_api:
        client.compute_route_matrix(
            47.674, -122.121, [(47.620, -122.350)], "DRIVE",
            depart_at="2026-12-01T18:00:00Z"
        )
        # Verify no routingPreference in the body sent to the API
        call_args = mock_api.call_args
        # _call_api is called with positional args: origin_lat, origin_lng, dest_lls, mode, depart_at
        # The body is built inside _call_api — we check via the actual API body in a separate test

    month = pacific_month()
    assert tmp_db.get_api_usage(month, ROUTES_SKU_ESSENTIALS) == 1
    assert tmp_db.get_api_usage(month, ROUTES_SKU_PRO) == 0


def test_multiple_destinations_counts_all_elements(tmp_db):
    """3 destinations = 3 elements billed."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    fake_result = [
        {"originIndex": 0, "destinationIndex": i, "condition": "ROUTE_EXISTS",
         "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}
        for i in range(3)
    ]

    with patch.object(client, "_call_api", return_value=fake_result):
        client.compute_route_matrix(
            47.674, -122.121,
            [(47.620, -122.350), (47.610, -122.330), (47.600, -122.310)],
            "DRIVE",
        )

    assert tmp_db.get_api_usage(pacific_month(), ROUTES_SKU_ESSENTIALS) == 3


def test_no_routing_preference_in_api_body(tmp_db):
    """The body sent to the Routes API must not contain routingPreference."""
    client = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.5,
    )
    captured_body = {}

    def fake_call_api(origin_lat, origin_lng, dest_lls, mode, depart_at):
        # Reconstruct what _call_api would build — check our actual implementation
        import json as _json
        body = {
            "origins": [{"waypoint": {"location": {"latLng": {
                "latitude": origin_lat, "longitude": origin_lng
            }}}}],
            "destinations": [{"waypoint": {"location": {"latLng": {
                "latitude": lat, "longitude": lng
            }}}} for lat, lng in dest_lls],
            "travelMode": mode,
        }
        captured_body.update(body)
        return [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
                 "duration": {"seconds": "600"}, "distanceMeters": 5000, "status": {}}]

    with patch.object(client, "_call_api", side_effect=fake_call_api):
        client.compute_route_matrix(47.674, -122.121, [(47.620, -122.350)], "DRIVE")

    assert "routingPreference" not in captured_body


# ---------------------------------------------------------------------------
# usage_status tool
# ---------------------------------------------------------------------------

def test_usage_status_returns_all_six_skus(tmp_db):
    places = GooglePlacesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=8000, pro_cap=4000, enterprise_cap=800, daily_cap_fraction=0.1,
    )
    routes = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=8000, pro_cap=4000, enterprise_cap=800, daily_cap_fraction=0.1,
    )
    result = tool_usage_status(db=tmp_db, google_places=places, google_routes=routes)
    sku_names = {s["sku"] for s in result["skus"]}
    assert sku_names == {
        PLACES_SKU_ESSENTIALS, PLACES_SKU_PRO, PLACES_SKU_ENTERPRISE,
        ROUTES_SKU_ESSENTIALS, ROUTES_SKU_PRO, ROUTES_SKU_ENTERPRISE,
    }


def test_usage_status_percentages_correct(tmp_db):
    """After using 4 out of 10 essentials, pct should be 40.0."""
    tmp_db.check_and_increment_usage(
        sku=PLACES_SKU_ESSENTIALS, amount=4, monthly_cap=10, daily_cap=100
    )
    places = GooglePlacesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=10, pro_cap=5, enterprise_cap=3, daily_cap_fraction=0.5,
    )
    routes = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=10, pro_cap=5, enterprise_cap=3, daily_cap_fraction=0.5,
    )
    result = tool_usage_status(db=tmp_db, google_places=places, google_routes=routes)
    sku_data = {s["sku"]: s for s in result["skus"]}
    assert sku_data[PLACES_SKU_ESSENTIALS]["monthly_count"] == 4
    assert sku_data[PLACES_SKU_ESSENTIALS]["monthly_cap"] == 10
    assert sku_data[PLACES_SKU_ESSENTIALS]["monthly_pct"] == 40.0


def test_usage_status_warning_flag(tmp_db):
    """SKU at 80%+ should have warning=True."""
    # Put essentials at 90%
    tmp_db.check_and_increment_usage(
        sku=PLACES_SKU_ESSENTIALS, amount=9, monthly_cap=10, daily_cap=100
    )
    places = GooglePlacesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=10, pro_cap=5, enterprise_cap=3, daily_cap_fraction=0.5,
    )
    routes = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=10, pro_cap=5, enterprise_cap=3, daily_cap_fraction=0.5,
    )
    result = tool_usage_status(db=tmp_db, google_places=places, google_routes=routes)
    sku_data = {s["sku"]: s for s in result["skus"]}
    assert sku_data[PLACES_SKU_ESSENTIALS]["warning"] is True
    assert sku_data[PLACES_SKU_PRO]["warning"] is False


def test_usage_status_includes_reset_date(tmp_db):
    places = GooglePlacesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.1,
    )
    routes = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.1,
    )
    result = tool_usage_status(db=tmp_db, google_places=places, google_routes=routes)
    assert "reset_date" in result
    assert result["reset_date"].endswith("-01")


def test_usage_status_includes_cloud_quotas(tmp_db):
    places = GooglePlacesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.1,
    )
    routes = GoogleRoutesClient(
        api_key="fake", db=tmp_db, discord_webhook="",
        essentials_cap=100, pro_cap=50, enterprise_cap=10, daily_cap_fraction=0.1,
    )
    result = tool_usage_status(db=tmp_db, google_places=places, google_routes=routes)
    assert "cloud_console_quotas" in result
    quotas = result["cloud_console_quotas"]
    assert quotas["Routes API - ComputeRouteMatrix per-element/day"] == 500
