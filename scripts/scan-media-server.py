#!/usr/bin/env python3
"""
scan-media-server: ClamAV scanning HTTP sidecar for Radarr/Sonarr.

Called via scan-media.sh which is mounted into Radarr/Sonarr containers as a
Custom Script (On Import trigger). Scans the parent directory of the imported
file recursively, deletes infected files, blocklists the release in the
originating service, and sends a Discord notification.

Configuration via environment variables:
  DISCORD_WEBHOOK_URL   Discord webhook for infection/error notifications
  RADARR_URL            default http://radarr:7878
  RADARR_API_KEY        Radarr API key
  SONARR_URL            default http://sonarr:8989
  SONARR_API_KEY        Sonarr API key
  LISTEN_HOST           default 0.0.0.0
  LISTEN_PORT           default 8080
  CLAMD_SOCKET          default /var/run/clamav/clamd.ctl
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
CLAMD_SOCKET = os.environ.get("CLAMD_SOCKET", "/var/run/clamav/clamd.ctl")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

RADARR_URL = os.environ.get("RADARR_URL", "http://radarr:7878").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
SONARR_URL = os.environ.get("SONARR_URL", "http://sonarr:8989").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("scan-media")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def notify_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        payload = json.dumps({"content": message}).encode()
        req = urlrequest.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "scan-media/1.0"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        log.warning("Discord notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------

def blocklist_release(service: str, media_id: str, download_id: str, source_title: str) -> None:
    """Add the release to Radarr or Sonarr blocklist so it is never grabbed again."""
    if service == "radarr":
        url = f"{RADARR_URL}/api/v3/blocklist"
        api_key = RADARR_API_KEY
        payload: dict = {
            "movieId": int(media_id) if media_id else 0,
            "sourceTitle": source_title,
            "protocol": "torrent",
            "indexer": "",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "quality": {
                "quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0, "modifier": "none"},
                "revision": {"version": 1, "real": 0, "isRepack": False},
            },
        }
    elif service == "sonarr":
        url = f"{SONARR_URL}/api/v3/blocklist"
        api_key = SONARR_API_KEY
        payload = {
            "seriesId": int(media_id) if media_id else 0,
            "sourceTitle": source_title,
            "protocol": "torrent",
            "indexer": "",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "quality": {
                "quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0},
                "revision": {"version": 1, "real": 0, "isRepack": False},
            },
            "episodeIds": [],
        }
    else:
        log.warning("Unknown service '%s', skipping blocklist", service)
        return

    try:
        data = json.dumps(payload).encode()
        req = urlrequest.Request(
            url,
            data=data,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=10) as resp:
            log.info("Blocklisted release in %s (status %s): %s", service, resp.status, source_title)
    except Exception as exc:
        log.warning("Failed to blocklist in %s: %s", service, exc)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_directory(path: str) -> tuple[int, list[str]]:
    """
    Run clamscan recursively on the parent directory of the given path.
    Uses the definitions already loaded by clamd (/var/lib/clamav) to avoid
    reloading on every scan. clamscan is the standalone scanner — no daemon
    client binary (clamdscan) is available in debian:12-slim's clamav package.
    Returns (exit_code, list_of_infected_file_paths).
    Exit codes: 0=clean, 1=infected, 2=error.
    """
    scan_dir = str(Path(path).parent)
    log.info("Scanning directory: %s", scan_dir)

    try:
        result = subprocess.run(
            ["clamscan", "--recursive", "--no-summary", scan_dir],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
        )
    except subprocess.TimeoutExpired:
        log.error("clamscan timed out for %s", scan_dir)
        return 2, []
    except FileNotFoundError:
        log.error("clamscan not found")
        return 2, []

    infected: list[str] = []
    for line in result.stdout.splitlines():
        # clamdscan output format: /path/to/file: Virus.Name FOUND
        if line.endswith(" FOUND"):
            infected_path = line.split(":")[0].strip()
            infected.append(infected_path)
            log.warning("INFECTED: %s", infected_path)

    log.info(
        "Scan complete (exit %d): %d infected file(s) in %s",
        result.returncode, len(infected), scan_dir,
    )
    return result.returncode, infected


def delete_infected(infected: list[str]) -> list[str]:
    deleted: list[str] = []
    for f in infected:
        try:
            Path(f).unlink(missing_ok=True)
            deleted.append(f)
            log.info("Deleted: %s", f)
        except Exception as exc:
            log.error("Failed to delete %s: %s", f, exc)
    return deleted


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "scan-media/1.0"

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scan":
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._respond(400, {"error": f"invalid JSON: {exc}"})
            return

        file_path = body.get("path", "")
        download_id = body.get("download_id", "")
        service = body.get("service", "")
        media_id = body.get("media_id", "")

        if not file_path:
            self._respond(400, {"error": "missing path"})
            return

        if not Path(file_path).exists():
            log.warning("Path does not exist: %s", file_path)
            self._respond(200, {"status": "skipped", "reason": "path not found"})
            return

        exit_code, infected = scan_directory(file_path)
        display_name = Path(file_path).name

        if exit_code == 0:
            # Clean
            log.info("Clean: %s", file_path)
            self._respond(200, {"status": "clean"})

        elif exit_code == 1:
            # Infected — delete, blocklist, notify
            deleted = delete_infected(infected)
            source_title = Path(file_path).parent.name

            blocklist_release(service, media_id, download_id, source_title)

            infected_list = "\n".join(f"- `{Path(f).name}`" for f in deleted)
            notify_discord(
                f":biohazard: **Malicious file(s) detected and deleted**\n"
                f"**Release:** {source_title}\n"
                f"**Service:** {service.capitalize()}\n"
                f"**Files nuked:**\n{infected_list}\n"
                f"Release has been blocklisted — it will not be grabbed again."
            )
            log.error("INFECTED — deleted %d file(s), blocklisted in %s", len(deleted), service)
            # Return 500 so Radarr/Sonarr mark the import as failed
            self._respond(500, {"status": "infected", "deleted": deleted})

        else:
            # Scan error
            log.error("Scan error (exit %d) for %s", exit_code, file_path)
            notify_discord(
                f":warning: **ClamAV scan error** (exit {exit_code})\n"
                f"**File:** {display_name}\n"
                f"File has been kept — manual review recommended."
            )
            self._respond(200, {"status": "scan_error", "exit_code": exit_code})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    log.info(
        "scan-media-server listening on %s:%d (clamd socket: %s)",
        LISTEN_HOST, LISTEN_PORT, CLAMD_SOCKET,
    )
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
