#!/usr/bin/env python3
"""queue-cleaner.py

Sidecar cleanup for the Sonarr/Radarr download queues. Targets the specific
failure mode where Real-Debrid rejects an infringing torrent hash, rdt-client
returns a 500, and Sonarr/Radarr park the item in the queue with
`status=warning` and `errorMessage="qBittorrent is reporting an error"`
forever. Decluttarr's stock jobs do not match that pattern (see
docs/queue-cleaner.md for the full rationale).

For each configured *arr instance, this script:

  1. GETs /api/v3/queue?pageSize=100
  2. Flags records that match ALL of:
       - status        == "warning"
       - errorMessage  matches one of ERROR_PATTERNS (case-insensitive substring)
       - size          == 0
       - sizeleft      == 0
       - age since `added` >= MIN_AGE_MINUTES
  3. DELETEs each match with removeFromClient=true and blocklist=<BLOCKLIST>.

When DRY_RUN=true the script logs what it would do without issuing DELETEs.

Configuration is read from environment variables, intended to be supplied by
a systemd EnvironmentFile (see scripts/queue-cleaner.env.example). The script
uses only the Python 3 standard library so no venv is required.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ---------- helpers ----------------------------------------------------------


def log(msg: str) -> None:
    """Write a single line to stdout. systemd-journald picks this up."""
    print(msg, flush=True)


def env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        log(f"WARN  invalid integer for {name}={val!r}, using default {default}")
        return default


def env_list(name: str, default: list[str]) -> list[str]:
    val = os.environ.get(name)
    if val is None or val == "":
        return list(default)
    return [p.strip() for p in val.split(",") if p.strip()]


def parse_added(ts: str) -> datetime | None:
    """Parse Sonarr/Radarr ISO timestamps. They look like '2026-05-25T22:36:38Z'."""
    if not ts:
        return None
    try:
        # datetime.fromisoformat in 3.11+ handles 'Z'; older versions do not.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def http_request(
    method: str,
    url: str,
    api_key: str,
    timeout: int = 30,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Api-Key", api_key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""


# ---------- core -------------------------------------------------------------


def matches(
    item: dict,
    patterns: list[str],
    min_age_minutes: int,
    now: datetime,
) -> tuple[bool, str]:
    """Return (matched, reason). reason is a short human-readable explainer."""
    if item.get("status") != "warning":
        return False, f"status={item.get('status')!r}"

    err = (item.get("errorMessage") or "").strip()
    err_lower = err.lower()
    if not any(p.lower() in err_lower for p in patterns):
        return False, f"errorMessage={err!r} does not match patterns"

    if item.get("size") != 0:
        return False, f"size={item.get('size')} != 0"

    if item.get("sizeleft") != 0:
        return False, f"sizeleft={item.get('sizeleft')} != 0"

    added = parse_added(item.get("added", ""))
    if added is None:
        return False, "missing/invalid added timestamp"
    age_min = (now - added).total_seconds() / 60.0
    if age_min < min_age_minutes:
        return False, f"age={age_min:.1f}m < {min_age_minutes}m"

    return True, f"matched (age={age_min:.1f}m, err={err!r})"


def process_instance(
    name: str,
    base_url: str,
    api_key: str,
    patterns: list[str],
    min_age_minutes: int,
    blocklist: bool,
    dry_run: bool,
) -> int:
    """Process one *arr instance. Returns count of items acted on."""
    base_url = base_url.rstrip("/")
    queue_url = f"{base_url}/api/v3/queue?pageSize=100&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"

    log(f"INFO  [{name}] GET {queue_url}")
    status, body = http_request("GET", queue_url, api_key)
    if status != 200:
        log(f"ERROR [{name}] queue fetch failed: HTTP {status}: {body[:200]!r}")
        return 0

    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log(f"ERROR [{name}] could not parse queue response: {e}")
        return 0

    records = data.get("records", [])
    log(f"INFO  [{name}] {len(records)} queue item(s) returned")

    now = datetime.now(timezone.utc)
    acted = 0
    for item in records:
        item_id = item.get("id")
        title = item.get("title", "<no title>")
        matched, reason = matches(item, patterns, min_age_minutes, now)
        if not matched:
            log(f"DEBUG [{name}] skip id={item_id} title={title!r}: {reason}")
            continue

        prefix = "DRY   " if dry_run else "ACT   "
        log(
            f"{prefix}[{name}] delete id={item_id} title={title!r} "
            f"downloadId={item.get('downloadId')} reason={reason} "
            f"blocklist={blocklist}"
        )

        if dry_run:
            acted += 1
            continue

        params = urllib.parse.urlencode(
            {
                "removeFromClient": "true",
                "blocklist": "true" if blocklist else "false",
                "skipRedownload": "false",
            }
        )
        del_url = f"{base_url}/api/v3/queue/{item_id}?{params}"
        del_status, del_body = http_request("DELETE", del_url, api_key)
        if 200 <= del_status < 300:
            log(f"INFO  [{name}] deleted id={item_id}")
            acted += 1
        else:
            log(
                f"ERROR [{name}] DELETE id={item_id} failed: "
                f"HTTP {del_status}: {del_body[:200]!r}"
            )

    return acted


# ---------- main -------------------------------------------------------------

DEFAULT_PATTERNS = [
    "qBittorrent is reporting an error",
    "The download is stalled with no connections",
]


def main() -> int:
    dry_run = env_bool("DRY_RUN", True)
    blocklist = env_bool("BLOCKLIST", True)
    min_age_minutes = env_int("MIN_AGE_MINUTES", 15)
    patterns = env_list("ERROR_PATTERNS", DEFAULT_PATTERNS)

    log(
        f"INFO  queue-cleaner start dry_run={dry_run} blocklist={blocklist} "
        f"min_age_minutes={min_age_minutes} patterns={patterns}"
    )

    instances = []
    sonarr_url = os.environ.get("SONARR_URL", "").strip()
    sonarr_key = os.environ.get("SONARR_API_KEY", "").strip()
    if sonarr_url and sonarr_key:
        instances.append(("sonarr", sonarr_url, sonarr_key))
    else:
        log(
            "INFO  sonarr not configured (SONARR_URL / SONARR_API_KEY missing), skipping"
        )

    radarr_url = os.environ.get("RADARR_URL", "").strip()
    radarr_key = os.environ.get("RADARR_API_KEY", "").strip()
    if radarr_url and radarr_key:
        instances.append(("radarr", radarr_url, radarr_key))
    else:
        log(
            "INFO  radarr not configured (RADARR_URL / RADARR_API_KEY missing), skipping"
        )

    if not instances:
        log("ERROR no instances configured; nothing to do")
        return 0  # exit 0 so the timer keeps firing

    total = 0
    for name, url, key in instances:
        try:
            total += process_instance(
                name, url, key, patterns, min_age_minutes, blocklist, dry_run
            )
        except Exception as e:  # noqa: BLE001 - never let one instance kill the run
            log(f"ERROR [{name}] unhandled exception: {e!r}")

    verb = "would delete" if dry_run else "deleted"
    log(f"INFO  queue-cleaner done: {verb} {total} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
