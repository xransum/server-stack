#!/usr/bin/env python3
"""queue-cleaner.py

Sidecar cleanup for the Sonarr/Radarr download queues. Handles five distinct
failure modes that leave queue items stuck or duplicated forever:

  Mode 1 -- rdt-client zero-size stall (ERROR_PATTERNS)
  ------------------------------------------------------
  Real-Debrid rejects an infringing torrent hash; rdt-client returns a 500;
  Sonarr/Radarr park the item with status=warning, size=0, sizeleft=0.
  Action: DELETE + blocklist=true + skipRedownload=false (triggers re-search).

  Mode 2 -- import failure / stalled (IMPORT_FAIL_PATTERNS)
  ----------------------------------------------------------
  The download completed but Sonarr could not import one or more expected
  episode files, OR the download stalled with no connections (partially
  downloaded or zero-byte). Unlike Mode 1, the size==0 guard is NOT applied.
  Age gate still applies.
  Action: DELETE + blocklist=true + skipRedownload=false (triggers re-search).

  Mode 3 -- force upgrade (FORCE_UPGRADE_PATTERNS)
  -------------------------------------------------
  Sonarr/Radarr rejected a completed download as "not an upgrade" because the
  existing file meets or exceeds the quality cutoff. Forces the import via
  ManualImport API, bypassing quality profile checks.
  Action: POST ManualImport, then DELETE with blocklist=false + skipRedownload=true.
  Only active when FORCE_UPGRADE=true.

  Mode 4 -- parse failure (PARSE_FAIL_PATTERNS)
  ----------------------------------------------
  Sonarr could not parse the release to determine which episode it belongs to.
  FDH already handles re-search; we blocklist and clear without triggering
  another search ourselves (avoids piling on more grabs).
  Action: DELETE + blocklist=true + skipRedownload=true (no re-search).

  Mode 5 -- duplicate grab detection (DEDUP_GRABS)
  -------------------------------------------------
  FDH grab storms: multiple releases downloading simultaneously for the same
  episode (same series title + SxxExx parsed from release title). Keeps the
  highest-resolution grab (or any completed item) and removes the rest.
  Only active when DEDUP_GRABS=true. Requires DEDUP_MIN_GRABS or more grabs
  for the same episode before acting.
  Action: DELETE + blocklist=false + skipRedownload=true for lower-quality dupes.

Configuration is read from environment variables. See queue-cleaner.env.example.
The script uses only the Python 3 standard library so no venv is required.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
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
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def http_request(
    method: str,
    url: str,
    api_key: str,
    body: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("X-Api-Key", api_key)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""


# ---------- match helpers ----------------------------------------------------


def _matches_patterns(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in patterns)


def _age_ok(item: dict, min_age_minutes: int, now: datetime) -> tuple[bool, str]:
    added = parse_added(item.get("added", ""))
    if added is None:
        # Sonarr sometimes omits the added timestamp for completed/importBlocked
        # items. Treat missing timestamp as infinitely old -- always passes.
        return True, "age=unknown (no timestamp, treating as old enough)"
    age_min = (now - added).total_seconds() / 60.0
    if age_min < min_age_minutes:
        return False, f"age={age_min:.1f}m < {min_age_minutes}m"
    return True, f"age={age_min:.1f}m"


def _status_messages_text(item: dict) -> str:
    """
    Flatten all statusMessages titles and messages into a single string for
    pattern matching. Sonarr uses statusMessages (not errorMessage) for
    importBlocked failures -- errorMessage is null in that case.
    """
    parts = []
    for sm in item.get("statusMessages") or []:
        if sm.get("title"):
            parts.append(sm["title"])
        for msg in sm.get("messages") or []:
            if msg:
                parts.append(msg)
    return " | ".join(parts)


# ---------- duplicate grab detection -----------------------------------------

# Resolution rank: higher = better. Used to pick which grab to keep.
_RES_RANK = {"2160": 5, "1080": 4, "720": 3, "576": 2, "480": 1}
_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[Xx Ee._-]?[Ee]?(\d{1,2})")
_RES_RE = re.compile(r"(\d{3,4})[pP]")


def _parse_episode_key(title: str) -> str | None:
    """
    Extract a normalised 'seriesslug:SxxExx' key from a release title.
    Returns None if no episode number found.
    """
    m = _SXXEXX_RE.search(title)
    if not m:
        return None
    season = int(m.group(1))
    episode = int(m.group(2))
    series_part = title[:m.start()].lower()
    series_slug = re.sub(r"[^a-z0-9]+", "", series_part).strip()
    if not series_slug:
        return None
    return f"{series_slug}:s{season:02d}e{episode:02d}"


def _resolution_rank(title: str) -> int:
    """Return a numeric quality rank from the release title. Higher = better."""
    m = _RES_RE.search(title)
    if not m:
        return 0
    res = m.group(1)
    for key in sorted(_RES_RANK, key=lambda k: -int(k)):
        if res.startswith(key):
            return _RES_RANK[key]
    return 0


def find_duplicate_grabs(
    records: list[dict],
    min_age_minutes: int,
    min_grabs: int,
    now: datetime,
) -> list[dict]:
    """
    Find queue items that are redundant duplicates of a better grab.

    Groups active items by episode key (series slug + SxxExx). For any group
    with >= min_grabs items where the oldest is >= min_age_minutes old:
    - If any item is completed, that one wins (file is already there)
    - Otherwise keep the highest-resolution downloading item
    - Return all non-winners as duplicates to remove

    Returns list of items to DELETE (blocklist=false, skipRedownload=true).
    """
    active_statuses = {"downloading", "completed", "delay", "downloadClientUnavailable"}

    by_episode: dict[str, list[dict]] = defaultdict(list)
    for item in records:
        if item.get("status") not in active_statuses:
            continue
        key = _parse_episode_key(item.get("title", ""))
        if key is None:
            continue
        by_episode[key].append(item)

    to_remove = []
    for key, items in by_episode.items():
        if len(items) < min_grabs:
            continue

        # Age gate: oldest item must be old enough
        oldest_age = max(
            (now - added).total_seconds() / 60.0
            for item in items
            if (added := parse_added(item.get("added", ""))) is not None
        ) if any(parse_added(i.get("added", "")) for i in items) else 0.0
        if oldest_age < min_age_minutes:
            continue

        completed = [i for i in items if i.get("status") == "completed"]
        winner = completed[0] if completed else max(
            items, key=lambda i: _resolution_rank(i.get("title", ""))
        )

        for item in items:
            if item.get("id") != winner.get("id"):
                to_remove.append(item)

    return to_remove


# ---------- delete helper ----------------------------------------------------


def delete_item(
    name: str,
    base_url: str,
    api_key: str,
    item_id: int,
    remove_from_client: bool,
    blocklist: bool,
    skip_redownload: bool,
    dry_run: bool,
    label: str = "",
) -> bool:
    """Issue a DELETE on a single queue item. Returns True if acted/would act."""
    suffix = f" {label}" if label else ""
    params = urllib.parse.urlencode({
        "removeFromClient": "true" if remove_from_client else "false",
        "blocklist": "true" if blocklist else "false",
        "skipRedownload": "true" if skip_redownload else "false",
    })
    del_url = f"{base_url}/api/v3/queue/{item_id}?{params}"
    if dry_run:
        log(
            f"DRY   [{name}] would delete id={item_id}{suffix} "
            f"removeFromClient={remove_from_client} blocklist={blocklist} skipRedownload={skip_redownload}"
        )
        return True
    del_status, del_body = http_request("DELETE", del_url, api_key)
    if 200 <= del_status < 300:
        log(f"INFO  [{name}] deleted id={item_id}{suffix}")
        return True
    elif del_status == 404:
        log(f"INFO  [{name}] id={item_id} already gone (HTTP 404)")
        return True
    else:
        log(f"ERROR [{name}] DELETE id={item_id} failed: HTTP {del_status}: {del_body[:200]!r}")
        return False


# ---------- force-import logic -----------------------------------------------


def force_import(
    name: str,
    base_url: str,
    api_key: str,
    item: dict,
    dry_run: bool,
) -> bool:
    download_id = item.get("downloadId")
    item_id = item.get("id")
    title = item.get("title", "<no title>")

    if not download_id:
        log(f"WARN  [{name}] force_upgrade id={item_id} title={title!r}: no downloadId, skipping force import")
        return False

    mi_url = (
        f"{base_url}/api/v3/manualimport"
        f"?downloadId={urllib.parse.quote(download_id)}"
        f"&filterExistingFiles=false"
    )
    log(f"INFO  [{name}] GET {mi_url}")
    mi_status, mi_body = http_request("GET", mi_url, api_key)
    if mi_status != 200:
        log(f"ERROR [{name}] manualimport lookup failed: HTTP {mi_status}: {mi_body[:200]!r}")
        return False

    try:
        mi_files = json.loads(mi_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log(f"ERROR [{name}] could not parse manualimport response: {e}")
        return False

    if not mi_files:
        log(f"WARN  [{name}] force_upgrade id={item_id} title={title!r}: manualimport returned 0 files")
        return False

    log(f"INFO  [{name}] force_upgrade id={item_id} title={title!r}: {len(mi_files)} file(s) to import")

    if dry_run:
        for f in mi_files:
            log(f"DRY   [{name}] would force-import path={f.get('path')!r}")
        return True

    cmd_payload = json.dumps({
        "name": "ManualImport",
        "files": mi_files,
        "importMode": "Move",
    }).encode("utf-8")

    cmd_url = f"{base_url}/api/v3/command"
    log(f"INFO  [{name}] POST {cmd_url} ManualImport importMode=Move files={len(mi_files)}")
    cmd_status, cmd_body = http_request("POST", cmd_url, api_key, body=cmd_payload)
    if 200 <= cmd_status < 300:
        log(f"INFO  [{name}] ManualImport command queued for id={item_id}")
        return True
    else:
        log(f"ERROR [{name}] ManualImport command failed: HTTP {cmd_status}: {cmd_body[:300]!r}")
        return False


# ---------- classify ---------------------------------------------------------


def classify(
    item: dict,
    patterns: list[str],
    import_fail_patterns: list[str],
    force_upgrade_patterns: list[str],
    force_upgrade_enabled: bool,
    parse_fail_patterns: list[str],
    min_age_minutes: int,
    now: datetime,
) -> tuple[str | None, str]:
    """
    Classify a queue item into one of the action modes.

    Returns (mode, reason) where mode is one of:
      "stall"         -- Mode 1
      "import_fail"   -- Mode 2
      "force_upgrade" -- Mode 3
      "parse_fail"    -- Mode 4
      None            -- no match, skip
    """
    status = item.get("status")
    tracked_state = item.get("trackedDownloadState", "")
    age_ok, age_reason = _age_ok(item, min_age_minutes, now)

    # Modes 2 and 4 -- importBlocked: error is in statusMessages, not errorMessage
    if status == "completed" and tracked_state == "importBlocked":
        sm_text = _status_messages_text(item)

        # Mode 4 -- parse fail (check before import_fail -- different action)
        if parse_fail_patterns and _matches_patterns(sm_text, parse_fail_patterns):
            if not age_ok:
                return None, f"parse_fail candidate but {age_reason}"
            return "parse_fail", f"parse_fail ({age_reason}, statusMessages={sm_text!r})"

        # Mode 2 -- generic import failure
        if import_fail_patterns and _matches_patterns(sm_text, import_fail_patterns):
            if not age_ok:
                return None, f"import_fail candidate but {age_reason}"
            return "import_fail", f"import_fail ({age_reason}, statusMessages={sm_text!r})"

        return None, f"importBlocked but statusMessages={sm_text!r} does not match any pattern"

    # Modes 1, 2 fallback, 3 require status=warning
    if status != "warning":
        return None, f"status={status!r}"

    err = (item.get("errorMessage") or "").strip()

    # Mode 3 -- force upgrade
    if force_upgrade_enabled and _matches_patterns(err, force_upgrade_patterns):
        if not age_ok:
            return None, f"force_upgrade candidate but {age_reason}"
        return "force_upgrade", f"force_upgrade ({age_reason}, err={err!r})"

    # Mode 2 fallback -- some *arr versions put import errors in errorMessage
    if import_fail_patterns and _matches_patterns(err, import_fail_patterns):
        if not age_ok:
            return None, f"import_fail candidate but {age_reason}"
        return "import_fail", f"import_fail ({age_reason}, err={err!r})"

    # Mode 1 -- zero-size stall (requires size==0 AND sizeleft==0)
    if _matches_patterns(err, patterns):
        if item.get("size") != 0:
            return None, f"stall candidate but size={item.get('size')} != 0"
        if item.get("sizeleft") != 0:
            return None, f"stall candidate but sizeleft={item.get('sizeleft')} != 0"
        if not age_ok:
            return None, f"stall candidate but {age_reason}"
        return "stall", f"stall ({age_reason}, err={err!r})"

    return None, f"errorMessage={err!r} does not match any pattern"


# ---------- core -------------------------------------------------------------


def process_instance(
    name: str,
    base_url: str,
    api_key: str,
    patterns: list[str],
    import_fail_patterns: list[str],
    force_upgrade_patterns: list[str],
    force_upgrade_enabled: bool,
    parse_fail_patterns: list[str],
    dedup_grabs: bool,
    dedup_min_grabs: int,
    min_age_minutes: int,
    blocklist: bool,
    dry_run: bool,
) -> int:
    """Process one *arr instance. Returns count of items acted on."""
    base_url = base_url.rstrip("/")
    queue_url = (
        f"{base_url}/api/v3/queue"
        f"?pageSize=200"
        f"&includeUnknownSeriesItems=true"
        f"&includeUnknownMovieItems=true"
    )

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

    # ---- Mode 5: duplicate grab detection (run first, before per-item classify)
    if dedup_grabs:
        dupes = find_duplicate_grabs(records, min_age_minutes, dedup_min_grabs, now)
        seen_dl_ids: set[str] = set()
        for item in dupes:
            item_id = item.get("id")
            title = item.get("title", "<no title>")
            dl_id = item.get("downloadId") or f"__no_dl_id__:{item_id}"
            if dl_id in seen_dl_ids:
                log(f"DEBUG [{name}] dedup skip id={item_id} title={title!r}: sibling already queued for removal")
                continue
            seen_dl_ids.add(dl_id)
            log(
                f"{'DRY   ' if dry_run else 'ACT   '}[{name}] dedup id={item_id} "
                f"title={title!r} res_rank={_resolution_rank(title)} downloadId={dl_id}"
            )
            if delete_item(name, base_url, api_key, item_id,
                           remove_from_client=True, blocklist=False,
                           skip_redownload=True, dry_run=dry_run, label="(dedup)"):
                acted += 1

    # ---- Modes 1-4: per-item classify
    classified: list[tuple[dict, str, str]] = []
    seen_download_ids_for_delete: set[str] = set()

    for item in records:
        item_id = item.get("id")
        title = item.get("title", "<no title>")
        mode, reason = classify(
            item,
            patterns,
            import_fail_patterns,
            force_upgrade_patterns,
            force_upgrade_enabled,
            parse_fail_patterns,
            min_age_minutes,
            now,
        )
        if mode is None:
            log(
                f"DEBUG [{name}] skip id={item_id} title={title!r} "
                f"status={item.get('status')!r} trackedState={item.get('trackedDownloadState')!r}: {reason}"
            )
            continue
        classified.append((item, mode, reason))

    for item, mode, reason in classified:
        item_id = item.get("id")
        title = item.get("title", "<no title>")
        dl_id = item.get("downloadId") or f"__no_download_id__:{item_id}"

        if mode == "force_upgrade":
            log(
                f"{'DRY   ' if dry_run else 'ACT   '}[{name}] force_upgrade id={item_id} "
                f"title={title!r} downloadId={dl_id} reason={reason}"
            )
            imported = force_import(name, base_url, api_key, item, dry_run)
            if not imported and not dry_run:
                log(f"WARN  [{name}] force import failed for id={item_id}, leaving in queue")
                continue
            if delete_item(name, base_url, api_key, item_id,
                           remove_from_client=False, blocklist=False,
                           skip_redownload=True, dry_run=dry_run, label="(force_upgrade)"):
                acted += 1

        elif mode == "parse_fail":
            if dl_id in seen_download_ids_for_delete:
                log(f"DEBUG [{name}] dedupe id={item_id} title={title!r}: sibling already queued")
                continue
            seen_download_ids_for_delete.add(dl_id)
            log(
                f"{'DRY   ' if dry_run else 'ACT   '}[{name}] parse_fail id={item_id} "
                f"title={title!r} downloadId={dl_id} reason={reason}"
            )
            # blocklist=true so it won't be re-grabbed, skipRedownload=true so we
            # don't trigger yet another search on top of whatever FDH already grabbed
            if delete_item(name, base_url, api_key, item_id,
                           remove_from_client=True, blocklist=True,
                           skip_redownload=True, dry_run=dry_run, label="(parse_fail)"):
                acted += 1

        else:
            # Mode 1 (stall) or Mode 2 (import_fail): blocklist + remove + redownload
            if dl_id in seen_download_ids_for_delete:
                log(f"DEBUG [{name}] dedupe id={item_id} title={title!r}: sibling already queued")
                continue
            seen_download_ids_for_delete.add(dl_id)
            log(
                f"{'DRY   ' if dry_run else 'ACT   '}[{name}] {mode} id={item_id} "
                f"title={title!r} downloadId={dl_id} reason={reason} blocklist={blocklist}"
            )
            if delete_item(name, base_url, api_key, item_id,
                           remove_from_client=True, blocklist=blocklist,
                           skip_redownload=False, dry_run=dry_run, label=f"({mode})"):
                acted += 1

    return acted


# ---------- main -------------------------------------------------------------

DEFAULT_PATTERNS = [
    "qBittorrent is reporting an error",
]

DEFAULT_IMPORT_FAIL_PATTERNS = [
    "One or more episodes expected in this release were not imported",
    "Failed to import episode",
    "The download is stalled with no connections",
]

DEFAULT_FORCE_UPGRADE_PATTERNS = [
    "Not an upgrade for existing episode file(s)",
    "Not an upgrade for existing movie file(s)",
]

DEFAULT_PARSE_FAIL_PATTERNS = [
    "Unable to parse download, automatic import is not possible",
]


def main() -> int:
    dry_run = env_bool("DRY_RUN", True)
    blocklist = env_bool("BLOCKLIST", True)
    min_age_minutes = env_int("MIN_AGE_MINUTES", 15)
    patterns = env_list("ERROR_PATTERNS", DEFAULT_PATTERNS)
    import_fail_patterns = env_list("IMPORT_FAIL_PATTERNS", DEFAULT_IMPORT_FAIL_PATTERNS)
    force_upgrade_enabled = env_bool("FORCE_UPGRADE", True)
    force_upgrade_patterns = env_list("FORCE_UPGRADE_PATTERNS", DEFAULT_FORCE_UPGRADE_PATTERNS)
    parse_fail_patterns = env_list("PARSE_FAIL_PATTERNS", DEFAULT_PARSE_FAIL_PATTERNS)
    dedup_grabs = env_bool("DEDUP_GRABS", True)
    dedup_min_grabs = env_int("DEDUP_MIN_GRABS", 2)

    log(
        f"INFO  queue-cleaner start dry_run={dry_run} blocklist={blocklist} "
        f"min_age_minutes={min_age_minutes} force_upgrade={force_upgrade_enabled} "
        f"dedup_grabs={dedup_grabs} dedup_min_grabs={dedup_min_grabs}"
    )
    log(f"INFO  ERROR_PATTERNS={patterns}")
    log(f"INFO  IMPORT_FAIL_PATTERNS={import_fail_patterns}")
    log(f"INFO  FORCE_UPGRADE_PATTERNS={force_upgrade_patterns}")
    log(f"INFO  PARSE_FAIL_PATTERNS={parse_fail_patterns}")

    instances = []
    sonarr_url = os.environ.get("SONARR_URL", "").strip()
    sonarr_key = os.environ.get("SONARR_API_KEY", "").strip()
    if sonarr_url and sonarr_key:
        instances.append(("sonarr", sonarr_url, sonarr_key))
    else:
        log("INFO  sonarr not configured (SONARR_URL / SONARR_API_KEY missing), skipping")

    radarr_url = os.environ.get("RADARR_URL", "").strip()
    radarr_key = os.environ.get("RADARR_API_KEY", "").strip()
    if radarr_url and radarr_key:
        instances.append(("radarr", radarr_url, radarr_key))
    else:
        log("INFO  radarr not configured (RADARR_URL / RADARR_API_KEY missing), skipping")

    if not instances:
        log("ERROR no instances configured; nothing to do")
        return 0

    total = 0
    for name, url, key in instances:
        try:
            total += process_instance(
                name,
                url,
                key,
                patterns,
                import_fail_patterns,
                force_upgrade_patterns,
                force_upgrade_enabled,
                parse_fail_patterns,
                dedup_grabs,
                dedup_min_grabs,
                min_age_minutes,
                blocklist,
                dry_run,
            )
        except Exception as e:  # noqa: BLE001 - never let one instance kill the run
            log(f"ERROR [{name}] unhandled exception: {e!r}")

    verb = "would act on" if dry_run else "acted on"
    log(f"INFO  queue-cleaner done: {verb} {total} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
