#!/usr/bin/env python3
"""torbox-blocklist.py

Watches the TorBox cloud torrent list for tasks that are stuck (stalled,
error) or completed-but-not-yet-cleaned-up, and takes corrective action:

  Mode A -- stalled / error torrents
  -----------------------------------
  TorBox reports a torrent with status 'stalled' or 'error' that has been in
  that state for longer than STUCK_MINUTES (default 5). The script matches the
  TorBox task against the Sonarr and/or Radarr download queue by title, then:
    - Blocklists the item in *arr (triggers re-search for a new release)
    - Deletes the TorBox task via DELETE /torrents/controltorrent

  If no queue match is found (orphaned TorBox task with no *arr owner):
    - Deletes the TorBox task only

  Mode B -- completed cloud cleanup (PR #8 gap in TorBoxarr)
  -----------------------------------------------------------
  TorBoxarr does not delete completed TorBox tasks after local import (upstream
  PR #8 not merged). Tasks accumulate in the TorBox cloud list indefinitely.
  For TorBox tasks with status 'completed' or 'cached' that are older than
  COMPLETED_CLEANUP_MINUTES (default 60), the script checks whether Sonarr /
  Radarr have already imported the content (item no longer in their queue).
  If the *arr queue no longer contains the item: delete the TorBox task.
  If the item is still in a *arr queue: leave it alone (download in progress).

Configuration
-------------
All config via environment variables. See docker/torbox-blocklist/example.env.

The script uses only the Python 3 standard library. No pip, no venv required.
"""

from __future__ import annotations

import json
import os
import re
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


def parse_dt(ts: str | None) -> datetime | None:
    """Parse ISO 8601 timestamps returned by the TorBox API."""
    if not ts:
        return None
    try:
        ts = ts.strip()
        # Strip sub-second precision beyond 6 digits (Python limit)
        if "." in ts:
            base, frac = ts.split(".", 1)
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
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, object]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    # TorBox's CDN (Cloudflare) blocks the default Python-urllib user agent.
    req.add_header("User-Agent", "Mozilla/5.0")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resp.status, {}
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, {}


# ---------------------------------------------------------------------------
# TorBox API
# ---------------------------------------------------------------------------

TORBOX_BASE = "https://api.torbox.app/v1/api"
STUCK_STATUSES = {"stalled", "error"}
DONE_STATUSES = {"completed", "cached", "seeding", "uploading", "paused"}


def torbox_list(api_key: str) -> list[dict]:
    """Fetch all torrents from TorBox."""
    url = f"{TORBOX_BASE}/torrents/mylist?bypass_cache=true"
    status, data = http_json("GET", url, headers={"Authorization": f"Bearer {api_key}"})
    if status == 429:
        log("WARN  [torbox] rate limited (429) -- will retry next cycle")
        return []
    if status != 200:
        log(f"ERROR [torbox] mylist returned HTTP {status}")
        return []
    if not isinstance(data, dict):
        return []
    return data.get("data", []) or []


def torbox_delete(api_key: str, torrent_id: int, dry_run: bool) -> bool:
    """Delete a torrent from TorBox cloud."""
    if dry_run:
        log(f"DRY   [torbox] would delete torrent id={torrent_id}")
        return True
    url = f"{TORBOX_BASE}/torrents/controltorrent"
    body = json.dumps({"torrent_id": torrent_id, "operation": "delete"}).encode()
    status, data = http_json(
        "DELETE", url,
        headers={"Authorization": f"Bearer {api_key}"},
        body=body,
    )
    if 200 <= status < 300:
        log(f"INFO  [torbox] deleted torrent id={torrent_id}")
        return True
    log(f"ERROR [torbox] delete id={torrent_id} returned HTTP {status}: {data!r}")
    return False


# ---------------------------------------------------------------------------
# *arr interaction
# ---------------------------------------------------------------------------


def arr_get_queue(base_url: str, api_key: str, app: str) -> list[dict]:
    url = (
        f"{base_url}/api/v3/queue"
        "?pageSize=200&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
    )
    status, data = http_json("GET", url, headers={"X-Api-Key": api_key})
    if status != 200:
        log(f"ERROR [{app}] queue fetch failed: HTTP {status}")
        return []
    if not isinstance(data, dict):
        return []
    return data.get("records", [])


def arr_blocklist(
    base_url: str, api_key: str, queue_id: int, app: str, dry_run: bool
) -> bool:
    if dry_run:
        log(f"DRY   [{app}] would blocklist queue id={queue_id}")
        return True
    params = urllib.parse.urlencode(
        {"blocklist": "true", "removeFromClient": "true", "skipRedownload": "false"}
    )
    url = f"{base_url}/api/v3/queue/{queue_id}?{params}"
    status, _ = http_json("DELETE", url, headers={"X-Api-Key": api_key})
    if 200 <= status < 300:
        log(f"INFO  [{app}] blocklisted queue id={queue_id}")
        return True
    if status == 404:
        log(f"INFO  [{app}] queue id={queue_id} already gone (HTTP 404)")
        return True
    log(f"ERROR [{app}] blocklist id={queue_id} returned HTTP {status}")
    return False


def arr_trigger_search(
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
    body = json.dumps({"name": command, id_field: [id_value]}).encode()
    status, _ = http_json(
        "POST", f"{base_url}/api/v3/command",
        headers={"X-Api-Key": api_key},
        body=body,
    )
    if 200 <= status < 300:
        log(f"INFO  [{app}] triggered {command} for {id_field}={id_value}")
    else:
        log(f"ERROR [{app}] {command} returned HTTP {status}")


# ---------------------------------------------------------------------------
# Name matching (copied from rdt-blocklist.py)
# ---------------------------------------------------------------------------


def names_match(tb_name: str, queue_title: str) -> bool:
    """Loose substring match: either name contains the other (case-insensitive)."""
    a = tb_name.lower()
    b = queue_title.lower()
    return a in b or b in a


_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[Xx Ee._-]?[Ee]?(\d{1,2})")


def _extract_sxxexx(title: str) -> str | None:
    m = _SXXEXX_RE.search(title)
    if not m:
        return None
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def _series_slug(title: str) -> str:
    m = _SXXEXX_RE.search(title)
    if not m:
        return re.sub(r"[^a-z0-9]", "", title.lower())
    return re.sub(r"[^a-z0-9]", "", title[:m.start()].lower())


def episode_match(tb_name: str, queue_title: str) -> bool:
    """
    Fallback match: same SxxExx + series slugs share a 4-char common prefix.
    Handles cases where the TorBox torrent name and *arr queue title differ
    (e.g. different release groups for the same episode).
    """
    tb_ep = _extract_sxxexx(tb_name)
    q_ep = _extract_sxxexx(queue_title)
    if not tb_ep or not q_ep or tb_ep != q_ep:
        return False
    tb_slug = _series_slug(tb_name)
    q_slug = _series_slug(queue_title)
    if not tb_slug or not q_slug:
        return False
    min_len = min(len(tb_slug), len(q_slug))
    prefix_len = sum(1 for i in range(min_len) if tb_slug[i] == q_slug[i])
    return prefix_len >= 4


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def handle_stuck(
    stuck: list[dict],
    sonarr_url: str,
    sonarr_key: str,
    radarr_url: str,
    radarr_key: str,
    api_key: str,
    dry_run: bool,
) -> None:
    """Mode A: blocklist stuck torrents in *arr and delete from TorBox."""
    apps = []
    if sonarr_url and sonarr_key:
        apps.append(("sonarr", sonarr_url, sonarr_key, "episodeIds", "episodeId"))
    if radarr_url and radarr_key:
        apps.append(("radarr", radarr_url, radarr_key, "movieIds", "movieId"))

    # Pre-fetch queues once
    queues: dict[str, list[dict]] = {}
    for app, url, key, _, _ in apps:
        queues[app] = arr_get_queue(url, key, app)

    for torrent in stuck:
        tb_id = torrent["id"]
        tb_name = torrent["name"]
        reason = torrent["reason"]
        matched_any = False

        for app, url, key, id_field, media_id_key in apps:
            for item in queues[app]:
                title = item.get("title", "")
                if names_match(tb_name, title) or episode_match(tb_name, title):
                    queue_id = item["id"]
                    media_id = item.get(media_id_key)
                    log(
                        f"INFO  [{app}] matched '{tb_name}' -> queue id={queue_id} "
                        f"title={title!r} | {reason}"
                    )
                    matched_any = True
                    if arr_blocklist(url, key, queue_id, app, dry_run):
                        if media_id:
                            arr_trigger_search(url, key, id_field, media_id, app, dry_run)
                    break

        if not matched_any:
            log(f"INFO  [torbox] no *arr match for '{tb_name}' ({reason}) -- orphaned, deleting")

        torbox_delete(api_key, tb_id, dry_run)


def handle_cleanup(
    done: list[dict],
    sonarr_url: str,
    sonarr_key: str,
    radarr_url: str,
    radarr_key: str,
    api_key: str,
    dry_run: bool,
) -> None:
    """Mode B: delete completed TorBox tasks that *arr has already imported."""
    # Pre-fetch queues once
    all_titles: set[str] = set()
    if sonarr_url and sonarr_key:
        for item in arr_get_queue(sonarr_url, sonarr_key, "sonarr"):
            all_titles.add(item.get("title", "").lower())
    if radarr_url and radarr_key:
        for item in arr_get_queue(radarr_url, radarr_key, "radarr"):
            all_titles.add(item.get("title", "").lower())

    for torrent in done:
        tb_id = torrent["id"]
        tb_name = torrent["name"]

        # Check if still in any *arr queue
        still_queued = any(
            names_match(tb_name, t) or episode_match(tb_name, t)
            for t in all_titles
        )
        if still_queued:
            log(f"DEBUG [torbox] '{tb_name}' still in *arr queue, skipping cleanup")
            continue

        log(f"INFO  [torbox] cleanup: '{tb_name}' no longer in *arr queue, deleting from cloud")
        torbox_delete(api_key, tb_id, dry_run)


def run_once(config: dict) -> None:
    api_key = config["torbox_api_key"]
    dry_run = config["dry_run"]
    stuck_minutes = config["stuck_minutes"]
    cleanup_minutes = config["completed_cleanup_minutes"]

    torrents = torbox_list(api_key)
    if not torrents:
        log("INFO  [torbox] no torrents in list (or fetch failed)")
        return

    now = datetime.now(timezone.utc)
    stuck: list[dict] = []
    done: list[dict] = []

    for t in torrents:
        status = (t.get("download_state") or t.get("status") or "").lower()
        name = t.get("name") or t.get("display_name") or f"id={t.get('id')}"
        tb_id = t.get("id")
        created_at = t.get("created_at") or t.get("updated_at")
        created_dt = parse_dt(created_at)
        age_min = (now - created_dt).total_seconds() / 60.0 if created_dt else 0

        if status in STUCK_STATUSES and age_min >= stuck_minutes:
            stuck.append({
                "id": tb_id,
                "name": name,
                "reason": f"status={status!r} age={age_min:.0f}m >= {stuck_minutes}m",
            })
        elif status in DONE_STATUSES and age_min >= cleanup_minutes:
            done.append({
                "id": tb_id,
                "name": name,
                "reason": f"status={status!r} age={age_min:.0f}m >= {cleanup_minutes}m",
            })

    log(f"INFO  [torbox] {len(torrents)} total | {len(stuck)} stuck | {len(done)} cleanup-eligible")

    if stuck:
        handle_stuck(
            stuck,
            config.get("sonarr_url", ""), config.get("sonarr_api_key", ""),
            config.get("radarr_url", ""), config.get("radarr_api_key", ""),
            api_key, dry_run,
        )

    if done:
        handle_cleanup(
            done,
            config.get("sonarr_url", ""), config.get("sonarr_api_key", ""),
            config.get("radarr_url", ""), config.get("radarr_api_key", ""),
            api_key, dry_run,
        )


def main() -> int:
    config = {
        "torbox_api_key": os.environ.get("TORBOX_API_KEY", "").strip(),
        "sonarr_url": os.environ.get("SONARR_URL", "").strip(),
        "sonarr_api_key": os.environ.get("SONARR_API_KEY", "").strip(),
        "radarr_url": os.environ.get("RADARR_URL", "").strip(),
        "radarr_api_key": os.environ.get("RADARR_API_KEY", "").strip(),
        "stuck_minutes": env_int("STUCK_MINUTES", 5),
        "completed_cleanup_minutes": env_int("COMPLETED_CLEANUP_MINUTES", 60),
        "dry_run": env_bool("DRY_RUN", True),
        "poll_interval": env_int("POLL_INTERVAL_SECONDS", 120),
    }

    if not config["torbox_api_key"]:
        log("ERROR TORBOX_API_KEY is not set")
        return 1

    one_shot = "--once" in sys.argv or env_bool("ONE_SHOT", False)

    log(
        f"INFO  torbox-blocklist start "
        f"dry_run={config['dry_run']} "
        f"one_shot={one_shot} "
        f"poll_interval={config['poll_interval']}s "
        f"stuck_minutes={config['stuck_minutes']} "
        f"completed_cleanup_minutes={config['completed_cleanup_minutes']}"
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
