"""
db.py — SQLite schema, WAL mode, migrations, and seed data.

All database access goes through the Db class. Never import sqlite3 directly
from other modules; always use Db.conn() to get a connection.

WAL mode is set at open time. The DB file must be on a local filesystem —
do NOT use an NFS path (SQLite WAL locking is incompatible with NFS).
"""

import sqlite3
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — all DDL in one place, versioned via user_version pragma
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id          TEXT PRIMARY KEY,          -- uuid
    name        TEXT NOT NULL UNIQUE,
    timezone    TEXT NOT NULL,             -- IANA zone, e.g. America/New_York
    center_lat  REAL NOT NULL,
    center_lng  REAL NOT NULL,
    radius_km   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spots (
    id                  TEXT PRIMARY KEY,  -- uuid (internal, stable)
    place_id            TEXT UNIQUE,       -- Google place_id, null for manual spots
    area_id             TEXT NOT NULL REFERENCES areas(id),
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,     -- restaurant|bar|coffee|activity|outdoors|venue|other
    address             TEXT,
    lat                 REAL,
    lng                 REAL,
    timezone            TEXT,              -- IANA zone from Google or area fallback
    price_level         INTEGER,           -- 0-4 (Google scale)
    rating              REAL,
    user_rating_count   INTEGER,
    regular_hours_json  TEXT,              -- Google regularOpeningHours periods JSON
    current_hours_json  TEXT,              -- Google currentOpeningHours periods JSON (next ~7 days)
    hours_fetched_at    TEXT,              -- ISO-8601 UTC timestamp of last hours fetch
    occasions_json      TEXT DEFAULT '[]', -- JSON array of strings
    tags_json           TEXT DEFAULT '[]', -- JSON array of strings
    notes               TEXT,
    saved_at            TEXT NOT NULL      -- ISO-8601 UTC timestamp
);

CREATE TABLE IF NOT EXISTS visits (
    id          TEXT PRIMARY KEY,          -- uuid
    spot_id     TEXT NOT NULL REFERENCES spots(id),
    visited_at  TEXT NOT NULL,             -- ISO-8601 UTC
    rating      INTEGER,                   -- 1-5
    notes       TEXT,
    occasion    TEXT,
    with_whom   TEXT
);

-- OAuth: registered MCP clients (Claude performs DCR on first connect)
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id               TEXT PRIMARY KEY,
    client_secret_hash      TEXT,          -- SHA-256 hex; null for public clients
    redirect_uris_json      TEXT NOT NULL, -- JSON array
    client_name             TEXT,
    registered_at           TEXT NOT NULL
);

-- Single-use authorization codes (60s TTL, PKCE-bound)
CREATE TABLE IF NOT EXISTS auth_codes (
    code                TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL,
    redirect_uri        TEXT NOT NULL,
    code_challenge      TEXT NOT NULL,     -- S256 PKCE challenge
    scope               TEXT,
    expires_at          TEXT NOT NULL,     -- ISO-8601 UTC
    used                INTEGER NOT NULL DEFAULT 0
);

-- Access + refresh tokens (stored as SHA-256 hashes)
CREATE TABLE IF NOT EXISTS tokens (
    token_hash          TEXT PRIMARY KEY,  -- SHA-256 hex of raw token
    token_type          TEXT NOT NULL,     -- 'access' | 'refresh'
    client_id           TEXT NOT NULL,
    scope               TEXT,
    expires_at          TEXT NOT NULL,     -- ISO-8601 UTC
    refresh_token_hash  TEXT,              -- for access tokens: hash of paired refresh token
    access_token_hash   TEXT,              -- for refresh tokens: hash of current access token
    revoked             INTEGER NOT NULL DEFAULT 0
);

-- Google API usage counter (per SKU per month)
CREATE TABLE IF NOT EXISTS api_usage (
    month   TEXT NOT NULL,  -- YYYY-MM
    sku     TEXT NOT NULL,  -- 'essentials' | 'pro' | 'enterprise'
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, sku)
);

-- Rate limiting: failed auth attempts per IP
CREATE TABLE IF NOT EXISTS auth_failures (
    ip              TEXT NOT NULL,
    failed_at       TEXT NOT NULL,  -- ISO-8601 UTC
    endpoint        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spots_area      ON spots(area_id);
CREATE INDEX IF NOT EXISTS idx_spots_place     ON spots(place_id);
CREATE INDEX IF NOT EXISTS idx_visits_spot     ON visits(spot_id);
CREATE INDEX IF NOT EXISTS idx_tokens_expires  ON tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_codes_expires   ON auth_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_failures_ip     ON auth_failures(ip, failed_at);
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
        # Covers Gainesville, Centreville, Haymarket
    },
    {
        "id": "area-redmond-wa",
        "name": "Redmond WA",
        "timezone": "America/Los_Angeles",
        "center_lat": 47.6740,
        "center_lng": -122.1215,
        "radius_km": 25.0,
        # Covers Bellevue, Kirkland, Bothell
    },
]


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
        """Create tables and seed data on first run."""
        with self.conn() as c:
            # Check if schema_version table exists
            row = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            already_initialized = row is not None

            # Create all tables
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
                        current,
                        SCHEMA_VERSION,
                    )
                    self._migrate(c, current)
                    c.execute(
                        "UPDATE schema_version SET version = ?",
                        (SCHEMA_VERSION,),
                    )

    def _seed(self, c: sqlite3.Connection) -> None:
        """Insert seed areas on fresh database."""
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
        """Apply incremental migrations."""
        # Future migrations go here as elif from_version == N blocks.
        # For version 1 there is nothing to migrate from (fresh schema only).
        pass

    def increment_api_usage(self, month: str, sku: str, amount: int = 1) -> int:
        """Increment the API usage counter and return the new total."""
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

    def get_api_usage(self, month: str, sku: str) -> int:
        """Return current usage count for a SKU in a month."""
        with self.conn() as c:
            row = c.execute(
                "SELECT count FROM api_usage WHERE month = ? AND sku = ?",
                (month, sku),
            ).fetchone()
            return row[0] if row else 0

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
