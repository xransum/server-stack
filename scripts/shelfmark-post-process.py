#!/usr/bin/env python3
"""
shelfmark-post-process.py — Shelfmark Custom Script hook.

Called by Shelfmark after each successful download as:
    script "<target_path>"
with a JSON payload on stdin when CUSTOM_SCRIPT_JSON_PAYLOAD=true.

Pipeline:
1. Resolve the book path from JSON stdin (paths.final_paths[0]) or $1 fallback.
2. POST to scan-media sidecar. Only HTTP 200 + status=="clean" passes.
3. On clean: move ebook files to /mnt/media/downloads/books-ready (atomic rename).
4. On infected/error/exception: exit 1 → Shelfmark marks task Error.
   Files stay in books-incoming for manual review.

The script is intentionally strict (fail closed). Radarr/Sonarr are lenient on
scan errors; Shelfmark is not, because infected ebooks would land directly in
Calibre-Web.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

SCAN_URL = os.environ.get("SCAN_MEDIA_URL", "http://scan-media:8080/scan")
BOOKS_READY = Path(os.environ.get("BOOKS_READY_DIR", "/mnt/media/downloads/books-ready"))
SCAN_TIMEOUT = 280  # seconds — Shelfmark kills the script at 300s

EBOOK_EXTENSIONS = {
    ".epub", ".pdf", ".mobi", ".azw", ".azw3",
    ".cbz", ".cbr", ".djvu", ".fb2", ".lit", ".lrf",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [shelfmark-post-process] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("shelfmark-post-process")


def resolve_path() -> Path:
    """
    Try to get the book path from Shelfmark's JSON stdin payload first,
    fall back to $1 (first CLI argument).
    """
    # Try JSON stdin
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            # Shelfmark JSON payload: {"paths": {"final_paths": ["/path/to/book"]}, ...}
            final_paths = (
                data.get("paths", {}).get("final_paths")
                or data.get("final_paths")
                or []
            )
            if final_paths:
                p = Path(final_paths[0])
                log.info("Path from JSON stdin: %s", p)
                return p
    except Exception as exc:
        log.debug("JSON stdin parse failed (%s), falling back to $1", exc)

    # Fall back to first CLI argument
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        log.info("Path from argv[1]: %s", p)
        return p

    raise RuntimeError("No path: JSON stdin was empty/unparseable and no $1 argument given")


def post_scan(file_path: Path) -> dict:
    """
    POST to scan-media sidecar. Returns the parsed JSON response body.
    Raises on network error or non-2xx status (except 500 which we handle).
    """
    payload = json.dumps({
        "path": str(file_path),
        "service": "shelfmark",
        "download_id": "",
        "media_id": "",
    }).encode()

    req = urlrequest.Request(
        SCAN_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=SCAN_TIMEOUT) as resp:
            body = json.loads(resp.read().decode())
            log.info("scan-media response (200): %s", body)
            return body
    except urlrequest.HTTPError as exc:
        body_bytes = exc.read()
        try:
            body = json.loads(body_bytes.decode())
        except Exception:
            body = {"raw": body_bytes.decode(errors="replace")}
        log.error("scan-media HTTP %d: %s", exc.code, body)
        raise


def move_to_ready(book_path: Path) -> None:
    """
    Move ebook files from book_path (dir or file) into BOOKS_READY.
    Uses write-to-temp-then-rename so the watcher only sees finished files.
    """
    BOOKS_READY.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    if book_path.is_file():
        candidates = [book_path]
    elif book_path.is_dir():
        candidates = [
            f for f in book_path.rglob("*")
            if f.is_file() and f.suffix.lower() in EBOOK_EXTENSIONS
        ]
    else:
        log.warning("Path does not exist or is neither file nor dir: %s", book_path)
        return

    if not candidates:
        log.warning("No ebook files found under %s", book_path)
        return

    for src in candidates:
        dest = BOOKS_READY / src.name
        # Avoid collisions
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            dest = BOOKS_READY / f"{stem}_{src.parent.name}{suffix}"

        # Atomic rename within same filesystem (NFS mount); fall back to copy+delete
        try:
            src.rename(dest)
            log.info("Moved %s -> %s", src, dest)
        except OSError:
            # Cross-device or NFS rename edge case — copy then delete
            tmp = BOOKS_READY / (src.name + ".tmp")
            shutil.copy2(src, tmp)
            tmp.rename(dest)
            src.unlink(missing_ok=True)
            log.info("Copied+deleted %s -> %s", src, dest)


def main() -> int:
    try:
        book_path = resolve_path()
    except Exception as exc:
        log.error("Failed to resolve path: %s", exc)
        return 1

    if not book_path.exists():
        log.error("Path does not exist: %s", book_path)
        return 1

    log.info("Post-processing: %s", book_path)

    # --- Scan ---
    try:
        result = post_scan(book_path)
    except URLError as exc:
        log.error("Network error reaching scan-media: %s", exc)
        return 1
    except urlrequest.HTTPError as exc:
        # 500 = infected (already deleted by scan-media)
        log.error("scan-media returned HTTP %d — treating as infected/failed", exc.code)
        return 1
    except Exception as exc:
        log.error("Unexpected error during scan: %s", exc)
        return 1

    status = result.get("status", "")

    if status == "clean":
        log.info("Scan clean — moving to books-ready")
        try:
            move_to_ready(book_path)
        except Exception as exc:
            log.error("Failed to move files to books-ready: %s", exc)
            return 1
        return 0

    # Any other status (scan_error, skipped, infected, unknown) — fail closed
    log.error("Scan result '%s' — leaving files in books-incoming for manual review", status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
