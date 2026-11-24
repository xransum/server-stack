"""
db.py — SQLite schema, WAL mode, migrations, and seed data.

All database access goes through the Db class. Never import sqlite3 directly
from other modules; always use Db.conn() to get a connection.

WAL mode is set at open time. The DB file must be on a local filesystem —
do NOT use an NFS path (SQLite WAL locking is incompatible with NFS).

Schema versions:
  1 — initial schema (Phase 1)
  2 — adds travel_time_cache, api_usage_daily, cap_warnings_sent
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — all DDL in one place, versioned via schema_version table
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    timezone    TEXT NOT NULL,
    center_lat  REAL NOT NULL,
    center_lng  REAL NOT NULL,
    radius_km   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spots (
    id                  TEXT PRIMARY KEY,
    place_id            TEXT UNIQUE,
    area_id             TEXT NOT NULL REFERENCES areas(id),
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,
    address             TEXT,
    lat                 REAL,
    lng                 REAL,
    timezone            TEXT,
    price_level         INTEGER,
    rating              REAL,
    user_rating_count   INTEGER,
    regular_hours_json  TEXT,
    current_hours_json  TEXT,
    hours_fetched_at    TEXT,
    occasions_json      TEXT DEFAULT '[]',
    tags_json           TEXT DEFAULT '[]',
    notes               TEXT,
    saved_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    id          TEXT PRIMARY KEY,
    spot_id     TEXT NOT NULL REFERENCES spots(id),
    visited_at  TEXT NOT NULL,
    rating      INTEGER,
    notes       TEXT,
    occasion    TEXT,
    with_whom   TEXT
);

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id               TEXT PRIMARY KEY,
    client_secret_hash      TEXT,
    redirect_uris_json      TEXT NOT NULL,
    client_name             TEXT,
    registered_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code                TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL,
    redirect_uri        TEXT NOT NULL,
    code_challenge      TEXT NOT NULL,
    scope               TEXT,
    expires_at          TEXT NOT NULL,
    used                INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tokens (
    token_hash          TEXT PRIMARY KEY,
    token_type          TEXT NOT NULL,
    client_id           TEXT NOT NULL,
    scope               TEXT,
    expires_at          TEXT NOT NULL,
    refresh_token_hash  TEXT,
    access_token_hash   TEXT,
    revoked             INTEGER NOT NULL DEFAULT 0
);

-- Google API monthly usage counter (per SKU per Pacific-time month).
-- SKU values: essentials | pro | enterprise | routes_essentials | routes_pro | routes_enterprise
CREATE TABLE IF NOT EXISTS api_usage (
    month   TEXT NOT NULL,  -- YYYY-MM in America/Los_Angeles
    sku     TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, sku)
);

-- Google API daily usage counter (per SKU per UTC date).
-- Used for burst limiting: no single day may exceed daily_cap.
CREATE TABLE IF NOT EXISTS api_usage_daily (
    date    TEXT NOT NULL,  -- YYYY-MM-DD UTC
    sku     TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, sku)
);

-- Travel time cache for non-traffic requests (24h TTL).
-- Keyed by SHA-256 of normalised origin+destinations+mode.
CREATE TABLE IF NOT EXISTS travel_time_cache (
    cache_key   TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    cached_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

-- Tracks whether an 80% cap warning Discord notice has been sent
-- this month for each SKU, to avoid spamming.
CREATE TABLE IF NOT EXISTS cap_warnings_sent (
    month   TEXT NOT NULL,  -- YYYY-MM in America/Los_Angeles
    sku     TEXT NOT NULL,
    PRIMARY KEY (month, sku)
);

-- Rate limiting: failed auth attempts per IP
CREATE TABLE IF NOT EXISTS auth_failures (
    ip              TEXT NOT NULL,
    failed_at       TEXT NOT NULL,
    endpoint        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spots_area      ON spots(area_id);
CREATE INDEX IF NOT EXISTS idx_spots_place     ON spots(place_id);
CREATE INDEX IF NOT EXISTS idx_visits_spot     ON visits(spot_id);
CREATE INDEX IF NOT EXISTS idx_tokens_expires  ON tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_codes_expires   ON auth_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_failures_ip     ON auth_failures(ip, failed_at);
CREATE INDEX IF NOT EXISTS idx_cache_expires   ON travel_time_cache(expires_at);
"""

# DDL for only the Phase 2 tables — used by the migration path
_SCHEMA_V2_TABLES = """
CREATE TABLE IF NOT EXISTS api_usage_daily (
    date    TEXT NOT NULL,
    sku     TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, sku)
);

CREATE TABLE IF NOT EXISTS travel_time_cache (
    cache_key   TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    cached_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cap_warnings_sent (
    month   TEXT NOT NULL,
    sku     TEXT NOT NULL,
    PRIMARY KEY (month, sku)
);

CREATE INDEX IF NOT EXISTS idx_cache_expires ON travel_time_cache(expires_at);
"""

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_AREAS = [
    {
        "id": "area-manassas-va",
        "name": "Manassas VA",
        "timezone": "America/New_York",
        "center_lat": 38.7509,
        "center_lng": -77.4753,
        "radius_km": 25.0,
    },
    {
        "id": "area-redmond-wa",
        "name": "Redmond WA",
        "timezone": "America/Los_Angeles",
        "center_lat": 47.6740,
        "center_lng": -122.1215,
        "radius_km": 25.0,
    },
]


# ---------------------------------------------------------------------------
# Pacific time helpers
# ---------------------------------------------------------------------------

def pacific_month() -> str:
    """Return current month as YYYY-MM in America/Los_Angeles (Google billing timezone)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")


def pacific_date_str() -> str:
    """Return current date as YYYY-MM-DD in America/Los_Angeles."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def pacific_next_month_reset() -> str:
    """Return ISO date string of the first day of next month in Pacific time."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    # Advance to first of next month
    if now.month == 12:
        reset = now.replace(year=now.year + 1, month=1, day=1,
                            hour=0, minute=0, second=0, microsecond=0)
    else:
        reset = now.replace(month=now.month + 1, day=1,
                            hour=0, minute=0, second=0, microsecond=0)
    return reset.strftime("%Y-%m-%d")


def utc_date_str() -> str:
    """Return current date as YYYY-MM-DD UTC (used for daily burst counter)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Db class
# ---------------------------------------------------------------------------

class Db:
    """SQLite database wrapper. One instance per process."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._ensure_dir()
        self._init_schema()

    def _ensure_dir(self) -> None:
        p = Path(self._path)
        p.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager returning a connection with WAL + foreign keys."""
        c = sqlite3.connect(self._path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA foreign_keys = ON")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self.conn() as c:
            row = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            already_initialized = row is not None

            c.executescript(SCHEMA_SQL)

            if not already_initialized:
                log.info("Initializing new database at %s", self._path)
                c.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                self._seed(c)
            else:
                version = c.execute("SELECT version FROM schema_version").fetchone()
                current = version[0] if version else 0
                if current < SCHEMA_VERSION:
                    log.info(
                        "Migrating database from version %d to %d",
                        current, SCHEMA_VERSION,
                    )
                    self._migrate(c, current)
                    c.execute(
                        "UPDATE schema_version SET version = ?",
                        (SCHEMA_VERSION,),
                    )

    def _seed(self, c: sqlite3.Connection) -> None:
        for area in SEED_AREAS:
            c.execute(
                """
                INSERT OR IGNORE INTO areas
                    (id, name, timezone, center_lat, center_lng, radius_km)
                VALUES
                    (:id, :name, :timezone, :center_lat, :center_lng, :radius_km)
                """,
                area,
            )
        log.info("Seeded %d areas", len(SEED_AREAS))

    def _migrate(self, c: sqlite3.Connection, from_version: int) -> None:
        if from_version < 2:
            log.info("Applying migration v1 → v2: travel_time_cache, api_usage_daily, cap_warnings_sent")
            c.executescript(_SCHEMA_V2_TABLES)

    # -----------------------------------------------------------------------
    # Atomic API usage enforcement
    # -----------------------------------------------------------------------

    def check_and_increment_usage(
        self,
        sku: str,
        amount: int,
        monthly_cap: int,
        daily_cap: int,
    ) -> tuple[int, int]:
        """
        Atomically increment both monthly and daily counters for a SKU.

        Checks BEFORE committing — if either cap would be exceeded, rolls back
        the increment and raises CapExceededError.

        Returns (new_monthly_total, new_daily_total) on success.

        This is the ONLY way to increment API usage. Never call
        increment_api_usage directly from google.py.

        Monthly key: Pacific time YYYY-MM (matches Google billing cycle).
        Daily key: UTC date YYYY-MM-DD (burst limiting, slightly conservative).
        """
        from .google import CapExceededError  # avoid circular import at module level

        month = pacific_month()
        date = utc_date_str()
        reset_date = pacific_next_month_reset()

        with self.conn() as c:
            # --- Monthly ---
            c.execute(
                """
                INSERT INTO api_usage (month, sku, count)
                VALUES (?, ?, ?)
                ON CONFLICT (month, sku) DO UPDATE SET count = count + excluded.count
                """,
                (month, sku, amount),
            )
            new_monthly = c.execute(
                "SELECT count FROM api_usage WHERE month = ? AND sku = ?",
                (month, sku),
            ).fetchone()[0]

            if new_monthly > monthly_cap:
                # Roll back — subtract what we just added
                c.execute(
                    "UPDATE api_usage SET count = count - ? WHERE month = ? AND sku = ?",
                    (amount, month, sku),
                )
                raise CapExceededError(
                    sku=sku,
                    cap=monthly_cap,
                    current=new_monthly - amount,
                    reset_date=reset_date,
                    limit_type="monthly",
                )

            # --- Daily ---
            c.execute(
                """
                INSERT INTO api_usage_daily (date, sku, count)
                VALUES (?, ?, ?)
                ON CONFLICT (date, sku) DO UPDATE SET count = count + excluded.count
                """,
                (date, sku, amount),
            )
            new_daily = c.execute(
                "SELECT count FROM api_usage_daily WHERE date = ? AND sku = ?",
                (date, sku),
            ).fetchone()[0]

            if new_daily > daily_cap:
                # Roll back both increments
                c.execute(
                    "UPDATE api_usage SET count = count - ? WHERE month = ? AND sku = ?",
                    (amount, month, sku),
                )
                c.execute(
                    "UPDATE api_usage_daily SET count = count - ? WHERE date = ? AND sku = ?",
                    (amount, date, sku),
                )
                raise CapExceededError(
                    sku=sku,
                    cap=daily_cap,
                    current=new_daily - amount,
                    reset_date="tomorrow UTC",
                    limit_type="daily",
                )

        return new_monthly, new_daily

    def get_api_usage(self, month: str, sku: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT count FROM api_usage WHERE month = ? AND sku = ?",
                (month, sku),
            ).fetchone()
            return row[0] if row else 0

    def get_daily_usage(self, date: str, sku: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT count FROM api_usage_daily WHERE date = ? AND sku = ?",
                (date, sku),
            ).fetchone()
            return row[0] if row else 0

    # Keep the old increment_api_usage for the backup/non-critical path only.
    # google.py must use check_and_increment_usage instead.
    def increment_api_usage(self, month: str, sku: str, amount: int = 1) -> int:
        """Non-atomic increment. Only for internal maintenance. Do not use from google.py."""
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO api_usage (month, sku, count)
                VALUES (?, ?, ?)
                ON CONFLICT (month, sku) DO UPDATE SET count = count + excluded.count
                """,
                (month, sku, amount),
            )
            row = c.execute(
                "SELECT count FROM api_usage WHERE month = ? AND sku = ?",
                (month, sku),
            ).fetchone()
            return row[0] if row else amount

    # -----------------------------------------------------------------------
    # Cap warning tracking
    # -----------------------------------------------------------------------

    def has_warned_cap(self, month: str, sku: str) -> bool:
        with self.conn() as c:
            row = c.execute(
                "SELECT 1 FROM cap_warnings_sent WHERE month = ? AND sku = ?",
                (month, sku),
            ).fetchone()
            return row is not None

    def record_cap_warning(self, month: str, sku: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO cap_warnings_sent (month, sku) VALUES (?, ?)",
                (month, sku),
            )

    # -----------------------------------------------------------------------
    # Travel time cache
    # -----------------------------------------------------------------------

    def get_travel_time_cache(self, cache_key: str) -> dict | None:
        """Return cached result if present and not expired, else None."""
        import json
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.conn() as c:
            row = c.execute(
                "SELECT result_json FROM travel_time_cache WHERE cache_key = ? AND expires_at > ?",
                (cache_key, now_iso),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set_travel_time_cache(self, cache_key: str, result: dict) -> None:
        """Store a travel time result with a 24h TTL."""
        import json
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=24)).isoformat()
        with self.conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO travel_time_cache
                    (cache_key, result_json, cached_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, json.dumps(result), now.isoformat(), expires),
            )

    def prune_expired_cache(self) -> None:
        """Remove expired travel time cache entries."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.conn() as c:
            c.execute(
                "DELETE FROM travel_time_cache WHERE expires_at <= ?",
                (now_iso,),
            )

    # -----------------------------------------------------------------------
    # Auth helpers (unchanged from Phase 1)
    # -----------------------------------------------------------------------

    def record_auth_failure(self, ip: str, endpoint: str, now_iso: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO auth_failures (ip, failed_at, endpoint) VALUES (?, ?, ?)",
                (ip, now_iso, endpoint),
            )

    def count_auth_failures(self, ip: str, since_iso: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM auth_failures WHERE ip = ? AND failed_at >= ?",
                (ip, since_iso),
            ).fetchone()
            return row[0] if row else 0

    def prune_auth_failures(self, before_iso: str) -> None:
        with self.conn() as c:
            c.execute(
                "DELETE FROM auth_failures WHERE failed_at < ?",
                (before_iso,),
            )

    def prune_expired_tokens(self, now_iso: str) -> None:
        with self.conn() as c:
            c.execute(
                "DELETE FROM tokens WHERE expires_at < ? AND token_type = 'access'",
                (now_iso,),
            )
            c.execute(
                "DELETE FROM auth_codes WHERE expires_at < ?",
                (now_iso,),
            )
