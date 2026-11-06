#!/usr/bin/env python3
"""rdt-blocklist.py

Watches the rdt-client SQLite database for torrents stuck with a Real-Debrid
provider error (infringing file, task cancellation, etc.), finds the
corresponding item in the Sonarr and/or Radarr download queue, blocklists it,
and triggers a fresh search -- all without any human intervention.

Background
----------
When Real-Debrid rejects a torrent hash as infringing, rdt-client records the
error in its local database but does not propagate a failure signal back to
Sonarr/Radarr. From their perspective the download client (rdt-client posing as
qBittorrent) shows status=warning, trackedDownloadStatus=ok -- the item sits
in the queue indefinitely and Sonarr/Radarr never grab an alternative release.

queue-cleaner.py handles the zero-size stalled variant of this failure. This
script handles two additional failure modes:

  Mode A -- RDT provider error (original behaviour)
  --------------------------------------------------
  rdt-client recorded a concrete error from Real-Debrid (infringing file,
  task cancelled, etc.). The DB row has a non-empty Error column.
  Action: blocklist in *arr + delete from DB + trigger re-search.

  Mode B -- stuck in processing state (new)
  -----------------------------------------
  rdt-client sent the torrent to Real-Debrid and it entered a processing
  state (magnet_conversion, waiting_files_selection, etc.) but never
  progressed. No error is recorded; the item just sits forever. Detected
  by checking RdStatusRaw against a list of stuck states AND requiring that
  the row is older than STUCK_PROCESSING_MINUTES (default: 120).
  Action: same as Mode A -- blocklist + delete + re-search.

Configuration
-------------
All config via environment variables. See scripts/rdt-blocklist.env.example.

The script uses only the Python 3 standard library and sqlite3 (bundled).
No pip, no venv required.
"""

from __future__ import annotations

import json
import os
import re
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


def parse_dt(ts: str) -> datetime | None:
    """Parse rdt-client ISO timestamps which may include offset or Z suffix."""
    if not ts:
        return None
    try:
        ts = ts.strip()
        # Strip sub-second precision beyond 6 digits (Python limit)
        if "." in ts:
            base, frac = ts.split(".", 1)
            # frac may have offset appended e.g. "1234567+00:00"
            for sep in ("+", "-"):
                if sep in frac:
                    frac, off = frac.split(sep, 1)
                    frac = frac[:6]
                    ts = f"{base}.{frac}{sep}{off}"
                    break
            else:
                ts = f"{base}.{frac[:6]}"
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        return None


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

# RdStatusRaw values that indicate the torrent is stuck in a processing limbo.
# Real-Debrid should resolve these in seconds to minutes; anything older than
# STUCK_PROCESSING_MINUTES is considered permanently stuck.
DEFAULT_STUCK_STATUSES = [
    "magnet_conversion",
    "waiting_files_selection",
    "queued",
    "compressing",
    "uploading",
]


def get_stuck_torrents(
    db_path: str,
    error_patterns: list[str],
    stuck_statuses: list[str],
    stuck_processing_minutes: int,
) -> list[dict]:
    """
    Return Torrent rows that need action, covering two modes:

    Mode A: Error column is non-empty and matches an error_pattern.
    Mode B: RdStatusRaw matches a stuck_status AND row is older than
            stuck_processing_minutes with no Error set.
    """
    stuck = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        rows = con.execute(
            "SELECT TorrentId, RdName, Error, RdStatusRaw, Added FROM Torrents"
        ).fetchall()
        con.close()
    except Exception as e:
        log(f"ERROR [rdt] failed to read database {db_path!r}: {e}")
        return []

    now = datetime.now(timezone.utc)

    for torrent_id, name, error, rd_status_raw, added in rows:
        error = (error or "").strip()
        rd_status_raw = (rd_status_raw or "").strip().lower()
        name = name or ""

        # Mode A -- concrete RDT provider error
        if error and any(p.lower() in error.lower() for p in error_patterns):
            stuck.append({
                "id": torrent_id,
                "name": name,
                "error": error,
                "reason": f"provider error: {error!r}",
            })
            continue

        # Mode B -- stuck in processing state past the timeout
        if stuck_statuses and rd_status_raw in [s.lower() for s in stuck_statuses]:
            if not error:  # don't double-count Mode A
                added_dt = parse_dt(added or "")
                if added_dt is not None:
                    age_min = (now - added_dt).total_seconds() / 60.0
                    if age_min >= stuck_processing_minutes:
                        stuck.append({
                            "id": torrent_id,
                            "name": name,
                            "error": f"stuck in '{rd_status_raw}' for {age_min:.0f}m",
                            "reason": f"stuck processing: RdStatusRaw={rd_status_raw!r} age={age_min:.0f}m >= {stuck_processing_minutes}m",
                        })

    return stuck


def _docker_container_action(container_name: str, action: str) -> bool:
    """
    Stop or start a Docker container via the Unix socket.
    action: 'stop' or 'start'
    Returns True on success.
    """
    import socket as _socket
    sock_path = "/var/run/docker.sock"
    if not os.path.exists(sock_path):
        return False
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect(sock_path)
        path = f"/containers/{container_name}/{action}"
        req = f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        sock.sendall(req.encode())
        resp = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
        sock.close()
        status_line = resp.decode("utf-8", errors="replace").split("\r\n")[0]
        code = int(status_line.split(" ")[1]) if len(status_line.split(" ")) > 1 else 0
        return 200 <= code < 300 or code == 204
    except Exception as e:
        log(f"WARN  [rdt] docker {action} {container_name!r} failed: {e}")
        return False


def delete_rdt_torrents(db_path: str, torrent_ids: list[str], dry_run: bool) -> None:
    """Delete multiple torrent rows in one stop/start cycle."""
    if not torrent_ids:
        return
    if dry_run:
        for tid in torrent_ids:
            log(f"DRY   [rdt] would delete torrent {tid}")
        return

    container = "rdt-client"
    stopped = _docker_container_action(container, "stop")
    if stopped:
        log(f"INFO  [rdt] stopped {container} to release WAL lock")
        import time; time.sleep(2)
    else:
        log(f"WARN  [rdt] could not stop {container} via Docker socket -- attempting delete anyway")

    try:
        con = sqlite3.connect(db_path, timeout=10)
        for tid in torrent_ids:
            con.execute("DELETE FROM Downloads WHERE TorrentId=?", (tid,))
            con.execute("DELETE FROM Torrents WHERE TorrentId=?", (tid,))
            log(f"INFO  [rdt] deleted stuck torrent {tid}")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except Exception as e:
        log(f"ERROR [rdt] failed to delete torrents: {e}")
    finally:
        if stopped:
            import time; time.sleep(1)
            restarted = _docker_container_action(container, "start")
            if restarted:
                log(f"INFO  [rdt] restarted {container}")
            else:
                log(f"ERROR [rdt] failed to restart {container} -- manual restart required")


# ---------------------------------------------------------------------------
# *arr interaction
# ---------------------------------------------------------------------------


def get_queue(base_url: str, api_key: str, app: str) -> list[dict]:
    url = (
        f"{base_url}/api/v3/queue"
        "?pageSize=200&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
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


_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[Xx Ee._-]?[Ee]?(\d{1,2})")


def _extract_sxxexx(title: str) -> str | None:
    """Extract normalised SxxExx from a title, e.g. 'S02E01'."""
    m = _SXXEXX_RE.search(title)
    if not m:
        return None
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def _series_slug(title: str) -> str:
    """Lowercase alphanum prefix before the first SxxExx pattern."""
    m = _SXXEXX_RE.search(title)
    if not m:
        return re.sub(r"[^a-z0-9]", "", title.lower())
    return re.sub(r"[^a-z0-9]", "", title[:m.start()].lower())


def episode_match(rdt_name: str, queue_title: str) -> bool:
    """
    Fallback match when names_match fails.
    Succeeds if both titles share the same SxxExx AND their series slugs
    share a common prefix of at least 4 characters.
    Handles cases where the RDT torrent name and Sonarr queue title are
    different releases for the same episode (e.g. grabbed separately).
    """
    rdt_ep = _extract_sxxexx(rdt_name)
    q_ep = _extract_sxxexx(queue_title)
    if not rdt_ep or not q_ep or rdt_ep != q_ep:
        return False
    rdt_slug = _series_slug(rdt_name)
    q_slug = _series_slug(queue_title)
    if not rdt_slug or not q_slug:
        return False
    # Common prefix of at least 4 chars (enough to distinguish series)
    min_len = min(len(rdt_slug), len(q_slug))
    prefix_len = sum(1 for i in range(min_len) if rdt_slug[i] == q_slug[i])
    return prefix_len >= 4


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
    dry_run: bool,
) -> set[str]:
    """
    Match stuck torrents against the *arr queue, blocklist matches, trigger
    re-searches. Returns set of RDT TorrentIds that should be deleted from DB.
    """
    if not base_url or not api_key:
        log(f"INFO  [{app}] not configured, skipping")
        return set()

    base_url = base_url.rstrip("/")
    id_field = "episodeIds" if app.lower() == "sonarr" else "movieIds"
    media_id_key = "episodeId" if app.lower() == "sonarr" else "movieId"

    queue = get_queue(base_url, api_key, app)
    if not queue:
        return set()

    to_delete: set[str] = set()

    for torrent in stuck:
        matched = False
        for item in queue:
            title = item.get("title", "")
            match_type = None
            if names_match(torrent["name"], title):
                match_type = "title"
            elif episode_match(torrent["name"], title):
                match_type = "episode_fallback"
            if match_type is None:
                continue

            queue_id = item["id"]
            media_id = item.get(media_id_key)
            log(
                f"INFO  [{app}] matched torrent '{torrent['name']}' "
                f"-> queue id={queue_id} title={title!r} "
                f"match={match_type} | {torrent['reason']}"
            )
            matched = True

            if blocklist_queue_item(base_url, api_key, queue_id, app, dry_run):
                if media_id:
                    trigger_search(base_url, api_key, id_field, media_id, app, dry_run)
                to_delete.add(torrent["id"])
            break

        if not matched:
            log(
                f"INFO  [{app}] no queue match for '{torrent['name']}' "
                f"({torrent['reason']}) -- orphaned DB row, will delete"
            )
            to_delete.add(torrent["id"])

    return to_delete


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEFAULT_ERROR_PATTERNS = [
    "infringing file",
    "could not add to provider",
    "a task was canceled",
]


def run_once(config: dict) -> None:
    db_path = config["rdt_db"]
    dry_run = config["dry_run"]

    stuck = get_stuck_torrents(
        db_path,
        config["error_patterns"],
        config["stuck_statuses"],
        config["stuck_processing_minutes"],
    )
    if not stuck:
        log("INFO  [rdt] no stuck torrents found")
        return

    log(f"INFO  [rdt] found {len(stuck)} stuck RDT torrent(s)")
    for t in stuck:
        log(f"DEBUG   TorrentId={t['id']} name={t['name']!r} reason={t['reason']!r}")

    # Collect torrent IDs to delete across all *arr instances.
    # Union so each ID is deleted once regardless of how many apps matched it.
    to_delete: set[str] = set()

    to_delete |= process_app(
        "sonarr",
        config.get("sonarr_url", ""),
        config.get("sonarr_api_key", ""),
        stuck,
        dry_run,
    )
    to_delete |= process_app(
        "radarr",
        config.get("radarr_url", ""),
        config.get("radarr_api_key", ""),
        stuck,
        dry_run,
    )

    # One stop/start cycle for all DB deletes
    if to_delete:
        delete_rdt_torrents(db_path, list(to_delete), dry_run)


def main() -> int:
    config = {
        "rdt_db": os.environ.get("RDT_DB_PATH", "").strip(),
        "sonarr_url": os.environ.get("SONARR_URL", "").strip(),
        "sonarr_api_key": os.environ.get("SONARR_API_KEY", "").strip(),
        "radarr_url": os.environ.get("RADARR_URL", "").strip(),
        "radarr_api_key": os.environ.get("RADARR_API_KEY", "").strip(),
        "error_patterns": env_list("ERROR_PATTERNS", DEFAULT_ERROR_PATTERNS),
        "stuck_statuses": env_list("STUCK_PROCESSING_STATUSES", DEFAULT_STUCK_STATUSES),
        "stuck_processing_minutes": env_int("STUCK_PROCESSING_MINUTES", 5),
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
        f"error_patterns={config['error_patterns']} "
        f"stuck_statuses={config['stuck_statuses']} "
        f"stuck_processing_minutes={config['stuck_processing_minutes']}"
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
