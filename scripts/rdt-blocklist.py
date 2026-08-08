#!/usr/bin/env python3
"""rdt-blocklist.py

Watches the rdt-client SQLite database for torrents stuck with a Real-Debrid
provider error (infringing file, task cancellation, etc.), finds the
corresponding item in the Sonarr and/or Radarr download queue, blocklists it,
and triggers a fresh search — all without any human intervention.

Background
----------
When Real-Debrid rejects a torrent hash as infringing, rdt-client records the
error in its local database but does not propagate a failure signal back to
Sonarr/Radarr. From their perspective the download client (rdt-client posing as
qBittorrent) shows `status=warning, trackedDownloadStatus=ok` — the item sits
in the queue indefinitely and Sonarr/Radarr never grab an alternative release.

queue-cleaner.py handles the zero-size stalled variant of this failure. This
script handles the subset where the RDT database has a concrete provider error:
it has the torrent name so it can match the exact Sonarr/Radarr queue entry and
also clean up the stuck DB row. Both scripts can run simultaneously.

Configuration
-------------
All config via environment variables. See scripts/rdt-blocklist.env.example.

The script uses only the Python 3 standard library and sqlite3 (bundled).
No pip, no venv required.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
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


def http_json(
    method: str,
    url: str,
    api_key: str,
    timeout: int = 30,
) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Api-Key", api_key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resp.status, {}
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        try:
            return e.code, json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, {}


# ---------------------------------------------------------------------------
# RDT database
# ---------------------------------------------------------------------------


def get_stuck_torrents(db_path: str, patterns: list[str]) -> list[dict]:
    """Return rows from Torrents where Error matches any infringing pattern."""
    stuck = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT TorrentId, RdName, Error FROM Torrents "
            "WHERE Error IS NOT NULL AND Error != ''"
        ).fetchall()
        con.close()
    except Exception as e:
        log(f"ERROR [rdt] failed to read database {db_path!r}: {e}")
        return []

    for torrent_id, name, error in rows:
        if any(p.lower() in (error or "").lower() for p in patterns):
            stuck.append({"id": torrent_id, "name": name or "", "error": error or ""})
    return stuck


def delete_rdt_torrent(db_path: str, torrent_id: str, dry_run: bool) -> None:
    if dry_run:
        log(f"DRY   [rdt] would delete torrent {torrent_id}")
        return
    try:
        con = sqlite3.connect(db_path)
        con.execute("DELETE FROM Downloads WHERE TorrentId=?", (torrent_id,))
        con.execute("DELETE FROM Torrents WHERE TorrentId=?", (torrent_id,))
        con.commit()
        con.close()
        log(f"INFO  [rdt] deleted stuck torrent {torrent_id}")
    except Exception as e:
        log(f"ERROR [rdt] failed to delete torrent {torrent_id}: {e}")


# ---------------------------------------------------------------------------
# *arr interaction
# ---------------------------------------------------------------------------


def get_queue(base_url: str, api_key: str, app: str) -> list[dict]:
    url = (
        f"{base_url}/api/v3/queue"
        "?pageSize=100&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
    )
    status, data = http_json("GET", url, api_key)
    if status != 200:
        log(f"ERROR [{app}] queue fetch failed: HTTP {status}")
        return []
    if not isinstance(data, dict):
        return []
    return data.get("records", [])


def names_match(rdt_name: str, queue_title: str) -> bool:
    """Loose substring match: either name contains the other (case-insensitive)."""
    a = rdt_name.lower()
    b = queue_title.lower()
    return a in b or b in a


def blocklist_queue_item(
    base_url: str, api_key: str, queue_id: int, app: str, dry_run: bool
) -> bool:
    if dry_run:
        log(f"DRY   [{app}] would blocklist queue id={queue_id}")
        return True
    params = urllib.parse.urlencode(
        {"blocklist": "true", "removeFromClient": "true", "skipRedownload": "false"}
    )
    url = f"{base_url}/api/v3/queue/{queue_id}?{params}"
    status, _ = http_json("DELETE", url, api_key)
    if 200 <= status < 300:
        log(f"INFO  [{app}] blocklisted queue id={queue_id}")
        return True
    if status == 404:
        log(f"INFO  [{app}] queue id={queue_id} already gone (HTTP 404)")
        return True
    log(f"ERROR [{app}] blocklist queue id={queue_id} returned HTTP {status}")
    return False


def trigger_search(
    base_url: str,
    api_key: str,
    id_field: str,
    id_value: int,
    app: str,
    dry_run: bool,
) -> None:
    command = "EpisodeSearch" if id_field == "episodeIds" else "MoviesSearch"
    if dry_run:
        log(f"DRY   [{app}] would trigger {command} for {id_field}={id_value}")
        return
    req = urllib.request.Request(
        f"{base_url}/api/v3/command",
        data=json.dumps({"name": command, id_field: [id_value]}).encode(),
        method="POST",
    )
    req.add_header("X-Api-Key", api_key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log(f"INFO  [{app}] triggered {command} for {id_field}={id_value} (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        log(f"ERROR [{app}] {command} returned HTTP {e.code}")
    except Exception as e:
        log(f"ERROR [{app}] {command} failed: {e}")


def process_app(
    app: str,
    base_url: str,
    api_key: str,
    stuck: list[dict],
    db_path: str,
    dry_run: bool,
) -> None:
    if not base_url or not api_key:
        log(f"INFO  [{app}] not configured, skipping")
        return

    base_url = base_url.rstrip("/")
    id_field = "episodeIds" if app.lower() == "sonarr" else "movieIds"
    media_id_key = "episodeId" if app.lower() == "sonarr" else "movieId"

    queue = get_queue(base_url, api_key, app)
    if not queue:
        return

    # Track which RDT torrents we've already cleaned up this pass to avoid
    # double-deleting if multiple queue entries match the same torrent.
    cleaned_rdt: set[str] = set()

    for torrent in stuck:
        for item in queue:
            title = item.get("title", "")
            if not names_match(torrent["name"], title):
                continue

            queue_id = item["id"]
            media_id = item.get(media_id_key)
            log(
                f"INFO  [{app}] matched torrent '{torrent['name']}' "
                f"-> queue id={queue_id} title={title!r} | rdt error: {torrent['error']!r}"
            )

            if blocklist_queue_item(base_url, api_key, queue_id, app, dry_run):
                if media_id:
                    trigger_search(base_url, api_key, id_field, media_id, app, dry_run)
                if torrent["id"] not in cleaned_rdt:
                    delete_rdt_torrent(db_path, torrent["id"], dry_run)
                    cleaned_rdt.add(torrent["id"])
            break  # matched — move on to next stuck torrent


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS = [
    "infringing file",
    "could not add to provider",
    "a task was canceled",
]


def run_once(config: dict) -> None:
    db_path = config["rdt_db"]
    patterns = config["error_patterns"]
    dry_run = config["dry_run"]

    stuck = get_stuck_torrents(db_path, patterns)
    if not stuck:
        return

    log(f"INFO  found {len(stuck)} stuck/infringing RDT torrent(s)")
    for t in stuck:
        log(f"DEBUG   TorrentId={t['id']} name={t['name']!r} error={t['error']!r}")

    process_app(
        "sonarr",
        config.get("sonarr_url", ""),
        config.get("sonarr_api_key", ""),
        stuck,
        db_path,
        dry_run,
    )
    process_app(
        "radarr",
        config.get("radarr_url", ""),
        config.get("radarr_api_key", ""),
        stuck,
        db_path,
        dry_run,
    )


def main() -> int:
    config = {
        "rdt_db": os.environ.get("RDT_DB_PATH", "").strip(),
        "sonarr_url": os.environ.get("SONARR_URL", "").strip(),
        "sonarr_api_key": os.environ.get("SONARR_API_KEY", "").strip(),
        "radarr_url": os.environ.get("RADARR_URL", "").strip(),
        "radarr_api_key": os.environ.get("RADARR_API_KEY", "").strip(),
        "error_patterns": env_list("ERROR_PATTERNS", DEFAULT_PATTERNS),
        "dry_run": env_bool("DRY_RUN", True),
        "poll_interval": env_int("POLL_INTERVAL_SECONDS", 120),
    }

    if not config["rdt_db"]:
        log("ERROR RDT_DB_PATH is not set")
        return 1

    one_shot = "--once" in sys.argv or env_bool("ONE_SHOT", False)

    log(
        f"INFO  rdt-blocklist start dry_run={config['dry_run']} "
        f"one_shot={one_shot} poll_interval={config['poll_interval']}s "
        f"patterns={config['error_patterns']}"
    )

    if one_shot:
        run_once(config)
        return 0

    while True:
        try:
            run_once(config)
        except Exception as e:
            log(f"ERROR unhandled exception: {e!r}")
        time.sleep(config["poll_interval"])


if __name__ == "__main__":
    sys.exit(main())
