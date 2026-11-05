#!/usr/bin/env python3
"""queue-cleaner.py

Sidecar cleanup for the Sonarr/Radarr download queues. Handles three distinct
failure modes that leave queue items stuck forever:

  Mode 1 — rdt-client zero-size stall (ERROR_PATTERNS, original behaviour)
  -------------------------------------------------------------------------
  Real-Debrid rejects an infringing torrent hash; rdt-client returns a 500;
  Sonarr/Radarr park the item with status=warning, size=0, sizeleft=0.
  Action: DELETE + blocklist=true + skipRedownload=false (triggers re-search).

  Mode 2 — import failure safety net (IMPORT_FAIL_PATTERNS)
  ----------------------------------------------------------
  The download completed but Sonarr could not import one or more of the
  expected episode files (naming mismatch, partial season pack, corrupt file,
  etc.). Sonarr's built-in Failed Download Handling fires first, but can loop
  or stall. queue-cleaner catches what FDH misses.
  Unlike Mode 1, import failures have real file size so the size==0 guard is
  NOT applied. Age gate still applies.
  Action: DELETE + blocklist=true + skipRedownload=false (triggers re-search).

  Mode 3 — force upgrade (FORCE_UPGRADE_PATTERNS)
  ------------------------------------------------
  Sonarr/Radarr rejected a completed download as "not an upgrade" because the
  existing file meets or exceeds the quality cutoff — even though the user
  wants the new file (e.g. the existing file is broken, or a season pack
  contains episodes already imported individually at a different quality).
  Action: POST ManualImport command to force the file in regardless of quality
  profile, then DELETE from queue with blocklist=false + skipRedownload=true.
  Only active when FORCE_UPGRADE=true.

Configuration is read from environment variables. See queue-cleaner.env.example.
The script uses only the Python 3 standard library so no venv is required.
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


def _matches_patterns(err: str, patterns: list[str]) -> bool:
    err_lower = err.lower()
    return any(p.lower() in err_lower for p in patterns)


def _age_ok(item: dict, min_age_minutes: int, now: datetime) -> tuple[bool, str]:
    added = parse_added(item.get("added", ""))
    if added is None:
        return False, "missing/invalid added timestamp"
    age_min = (now - added).total_seconds() / 60.0
    if age_min < min_age_minutes:
        return False, f"age={age_min:.1f}m < {min_age_minutes}m"
    return True, f"age={age_min:.1f}m"


def _status_messages_text(item: dict) -> str:
    """
    Flatten all statusMessages titles and messages into a single string for
    pattern matching. Sonarr uses statusMessages (not errorMessage) for
    importBlocked failures — errorMessage is null in that case.
    """
    parts = []
    for sm in item.get("statusMessages") or []:
        if sm.get("title"):
            parts.append(sm["title"])
        for msg in sm.get("messages") or []:
            if msg:
                parts.append(msg)
    return " | ".join(parts)


def classify(
    item: dict,
    patterns: list[str],
    import_fail_patterns: list[str],
    force_upgrade_patterns: list[str],
    force_upgrade_enabled: bool,
    min_age_minutes: int,
    now: datetime,
) -> tuple[str | None, str]:
    """
    Classify a queue item into one of the action modes.

    Returns (mode, reason) where mode is one of:
      "stall"          — Mode 1: zero-size rdt-client stall → blocklist + redownload
      "import_fail"    — Mode 2: import failure safety net → blocklist + redownload
      "force_upgrade"  — Mode 3: not-an-upgrade → force import + clear queue
      None             — no match, skip

    reason is a short human-readable string for logging.

    Sonarr queue item shapes by failure type:
      Mode 1 (stall):        status=warning,   errorMessage=<string>, size=0, sizeleft=0
      Mode 2 (importBlocked):status=completed, trackedDownloadState=importBlocked,
                             errorMessage=null, statusMessages=[...] contains the error
      Mode 3 (not-upgrade):  status=warning,   errorMessage=<string>
    """
    status = item.get("status")
    tracked_state = item.get("trackedDownloadState", "")

    # Age gate applies to all modes
    age_ok, age_reason = _age_ok(item, min_age_minutes, now)

    # Mode 2 — importBlocked: status=completed, trackedDownloadState=importBlocked
    # Error text lives in statusMessages, not errorMessage.
    if status == "completed" and tracked_state == "importBlocked":
        if import_fail_patterns:
            sm_text = _status_messages_text(item)
            if _matches_patterns(sm_text, import_fail_patterns):
                if not age_ok:
                    return None, f"import_fail candidate but {age_reason}"
                return "import_fail", f"import_fail ({age_reason}, statusMessages={sm_text!r})"
            return None, f"importBlocked but statusMessages={sm_text!r} does not match IMPORT_FAIL_PATTERNS"
        return None, "importBlocked but IMPORT_FAIL_PATTERNS is empty"

    # Modes 1 and 3 require status=warning
    if status != "warning":
        return None, f"status={status!r}"

    err = (item.get("errorMessage") or "").strip()

    # Mode 3 — force upgrade (check before Mode 1 since error strings differ)
    if force_upgrade_enabled and _matches_patterns(err, force_upgrade_patterns):
        if not age_ok:
            return None, f"force_upgrade candidate but {age_reason}"
        return "force_upgrade", f"force_upgrade ({age_reason}, err={err!r})"

    # Mode 2 fallback — some *arr versions put import errors in errorMessage
    # with status=warning rather than importBlocked. Handle both.
    if import_fail_patterns and _matches_patterns(err, import_fail_patterns):
        if not age_ok:
            return None, f"import_fail candidate but {age_reason}"
        return "import_fail", f"import_fail ({age_reason}, err={err!r})"

    # Mode 1 — zero-size stall (requires size == 0 AND sizeleft == 0)
    if _matches_patterns(err, patterns):
        if item.get("size") != 0:
            return None, f"stall candidate but size={item.get('size')} != 0"
        if item.get("sizeleft") != 0:
            return None, f"stall candidate but sizeleft={item.get('sizeleft')} != 0"
        if not age_ok:
            return None, f"stall candidate but {age_reason}"
        return "stall", f"stall ({age_reason}, err={err!r})"

    return None, f"errorMessage={err!r} does not match any pattern"


# ---------- force-import logic -----------------------------------------------


def force_import(
    name: str,
    base_url: str,
    api_key: str,
    item: dict,
    dry_run: bool,
) -> bool:
    """
    Attempt to force-import the files associated with a queue item via the
    Sonarr/Radarr ManualImport command, which bypasses quality profile checks.

    Returns True if the import command was dispatched (or would be in dry-run),
    False on API errors.
    """
    download_id = item.get("downloadId")
    item_id = item.get("id")
    title = item.get("title", "<no title>")

    if not download_id:
        log(f"WARN  [{name}] force_upgrade id={item_id} title={title!r}: no downloadId, skipping force import")
        return False

    # Step 1: GET /api/v3/manualimport — ask Sonarr/Radarr what files it sees
    # filterExistingFiles=false so it includes files already tracked
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
            log(f"DRY   [{name}] would force-import path={f.get('path')!r} quality={f.get('quality', {}).get('quality', {}).get('name')!r}")
        return True

    # Step 2: POST /api/v3/command ManualImport with importMode=Move
    # Pass the file list back as-is; Sonarr/Radarr already resolved series/episode IDs.
    # importMode=Move replaces the existing file on disk.
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


# ---------- core -------------------------------------------------------------


def process_instance(
    name: str,
    base_url: str,
    api_key: str,
    patterns: list[str],
    import_fail_patterns: list[str],
    force_upgrade_patterns: list[str],
    force_upgrade_enabled: bool,
    min_age_minutes: int,
    blocklist: bool,
    dry_run: bool,
) -> int:
    """Process one *arr instance. Returns count of items acted on."""
    base_url = base_url.rstrip("/")
    queue_url = (
        f"{base_url}/api/v3/queue"
        f"?pageSize=100"
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

    # First pass: classify items and group by downloadId.
    # Sonarr creates one queue entry per episode for a season-pack torrent,
    # all sharing the same downloadId. For Mode 1/2 (stall/import_fail),
    # deleting one with removeFromClient=true cascades to all siblings —
    # dedupe to avoid 404s. For Mode 3 (force_upgrade), we need to act on
    # ALL siblings individually (each episode entry needs its own import).
    classified: list[tuple[dict, str, str]] = []  # (item, mode, reason)
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
            min_age_minutes,
            now,
        )
        if mode is None:
            log(f"DEBUG [{name}] skip id={item_id} title={title!r} status={item.get('status')!r} trackedState={item.get('trackedDownloadState')!r}: {reason}")
            continue
        classified.append((item, mode, reason))

    if not classified:
        return 0

    acted = 0
    for item, mode, reason in classified:
        item_id = item.get("id")
        title = item.get("title", "<no title>")
        dl_id = item.get("downloadId") or f"__no_download_id__:{item_id}"

        if mode == "force_upgrade":
            # Force import each sibling individually (each is a distinct episode).
            prefix = "DRY   " if dry_run else "ACT   "
            log(
                f"{prefix}[{name}] force_upgrade id={item_id} title={title!r} "
                f"downloadId={dl_id} reason={reason}"
            )
            imported = force_import(name, base_url, api_key, item, dry_run)
            if not imported and not dry_run:
                log(f"WARN  [{name}] force import failed for id={item_id}, leaving in queue")
                continue
            # Clear the queue item: no blocklist, no re-download
            if not dry_run:
                params = urllib.parse.urlencode({
                    "removeFromClient": "false",  # file already moved; don't touch rdt
                    "blocklist": "false",
                    "skipRedownload": "true",
                })
                del_url = f"{base_url}/api/v3/queue/{item_id}?{params}"
                del_status, del_body = http_request("DELETE", del_url, api_key)
                if 200 <= del_status < 300:
                    log(f"INFO  [{name}] cleared queue id={item_id} after force import")
                    acted += 1
                elif del_status == 404:
                    log(f"INFO  [{name}] id={item_id} already gone (HTTP 404)")
                    acted += 1
                else:
                    log(f"ERROR [{name}] DELETE id={item_id} failed: HTTP {del_status}: {del_body[:200]!r}")
            else:
                acted += 1

        else:
            # Mode 1 (stall) or Mode 2 (import_fail): blocklist + remove + redownload.
            # Dedupe by downloadId — cascade-removal handles siblings.
            if dl_id in seen_download_ids_for_delete:
                log(
                    f"DEBUG [{name}] dedupe id={item_id} title={title!r}: "
                    f"sibling of already-queued delete (downloadId={dl_id})"
                )
                continue
            seen_download_ids_for_delete.add(dl_id)

            prefix = "DRY   " if dry_run else "ACT   "
            log(
                f"{prefix}[{name}] {mode} id={item_id} title={title!r} "
                f"downloadId={dl_id} reason={reason} blocklist={blocklist}"
            )

            if dry_run:
                acted += 1
                continue

            params = urllib.parse.urlencode({
                "removeFromClient": "true",
                "blocklist": "true" if blocklist else "false",
                "skipRedownload": "false",
            })
            del_url = f"{base_url}/api/v3/queue/{item_id}?{params}"
            del_status, del_body = http_request("DELETE", del_url, api_key)
            if 200 <= del_status < 300:
                log(f"INFO  [{name}] deleted id={item_id} ({mode})")
                acted += 1
            elif del_status == 404:
                log(
                    f"INFO  [{name}] id={item_id} already gone (HTTP 404); "
                    f"likely cascade-removed or cleaned by another process"
                )
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

DEFAULT_IMPORT_FAIL_PATTERNS = [
    "One or more episodes expected in this release were not imported",
    "Failed to import episode",
]

DEFAULT_FORCE_UPGRADE_PATTERNS = [
    "Not an upgrade for existing episode file(s)",
    "Not an upgrade for existing movie file(s)",
]


def main() -> int:
    dry_run = env_bool("DRY_RUN", True)
    blocklist = env_bool("BLOCKLIST", True)
    min_age_minutes = env_int("MIN_AGE_MINUTES", 15)
    patterns = env_list("ERROR_PATTERNS", DEFAULT_PATTERNS)
    import_fail_patterns = env_list("IMPORT_FAIL_PATTERNS", DEFAULT_IMPORT_FAIL_PATTERNS)
    force_upgrade_enabled = env_bool("FORCE_UPGRADE", True)
    force_upgrade_patterns = env_list("FORCE_UPGRADE_PATTERNS", DEFAULT_FORCE_UPGRADE_PATTERNS)

    log(
        f"INFO  queue-cleaner start dry_run={dry_run} blocklist={blocklist} "
        f"min_age_minutes={min_age_minutes} force_upgrade={force_upgrade_enabled}"
    )
    log(f"INFO  ERROR_PATTERNS={patterns}")
    log(f"INFO  IMPORT_FAIL_PATTERNS={import_fail_patterns}")
    log(f"INFO  FORCE_UPGRADE_PATTERNS={force_upgrade_patterns}")

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
        return 0  # exit 0 so the timer keeps firing

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
