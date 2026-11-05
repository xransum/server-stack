# queue-cleaner

A small sidecar that polls the Sonarr and Radarr download queues on a timer
and removes items stuck in failure states that would otherwise block the
pipeline indefinitely.

- **Port**: none (outbound API client only)
- **Runs as**: `media` system user (native) / `nobody` (Docker)
- **Install path**: `/opt/queue-cleaner/`
- **Config**: `/etc/queue-cleaner.env`
- **Schedule**: every 5 minutes via systemd timer

## Why this exists

Sonarr and Radarr have built-in cleanup (Failed Download Handling, decluttarr)
but three specific failure modes slip through and leave items stuck forever:

### Mode 1 — rdt-client zero-size stall

Real-Debrid rejects an infringing torrent hash; rdt-client returns a 500;
Sonarr/Radarr park the item with `status=warning, size=0, sizeleft=0`.
Neither `remove_failed_downloads` (only acts on `status=failed`) nor
`remove_stalled` (wrong error string) catches this. A real example:

```json
{
  "status": "warning",
  "trackedDownloadStatus": "ok",
  "trackedDownloadState": "downloading",
  "errorMessage": "qBittorrent is reporting an error",
  "size": 0,
  "sizeleft": 0
}
```

**Action**: DELETE + blocklist + redownload (triggers a fresh search).

### Mode 2 — import failure safety net

The download completed with real file size but Sonarr could not import one or
more expected episode files (naming mismatch, partial season pack, corrupt
file, etc.). Sonarr's built-in Failed Download Handling fires first, but can
loop or stall when no better release exists. queue-cleaner catches what FDH
misses.

Unlike Mode 1, import failures have non-zero file size so the `size==0` guard
is **not** applied. The age gate still applies.

**Action**: DELETE + blocklist + redownload (triggers a fresh search).

### Mode 3 — force upgrade

Sonarr/Radarr rejected a completed download as "not an upgrade" because the
existing file meets or exceeds the quality cutoff. Common scenario: a season
pack is grabbed and one episode was already imported individually at a
different quality tier — Sonarr rejects that episode and flags the whole pack
as incomplete. If the existing file is broken or the user wants the new
version regardless of quality, the item sits in queue forever.

queue-cleaner calls the `/api/v3/manualimport` endpoint to force the files
in, bypassing quality profile checks, then clears the queue entry.

**Action**: POST ManualImport (bypasses quality check, replaces existing file)
→ DELETE from queue with no blocklist and no redownload.

> **Warning**: the existing file on disk is replaced. If the new file is
> actually lower quality and the old one was working, you lose the old one.
> Set `FORCE_UPGRADE=false` to disable this mode if that matters for your
> library.

## Prerequisites

- Python 3 (system `/usr/bin/python3`, stdlib only)
- Sonarr and/or Radarr reachable
- The `media` user must exist. See [permissions](permissions.md).

## Install

Pull the repo (if you haven't already):

```bash
git -C ~/Documents/Gits/server-stack pull
```

Drop the script into `/opt/queue-cleaner/` and hand it to `media`:

```bash
sudo mkdir -p /opt/queue-cleaner
sudo cp ~/Documents/Gits/server-stack/scripts/queue-cleaner.py /opt/queue-cleaner/
sudo chown -R media:media /opt/queue-cleaner
sudo chmod 755 /opt/queue-cleaner/queue-cleaner.py
```

Install the environment file. The keys are sensitive so it lives outside the
repo and is locked down to root:

```bash
sudo cp ~/Documents/Gits/server-stack/scripts/queue-cleaner.env.example /etc/queue-cleaner.env
sudo chown root:root /etc/queue-cleaner.env
sudo chmod 600 /etc/queue-cleaner.env
sudo nano /etc/queue-cleaner.env
```

Fill in `SONARR_API_KEY` and `RADARR_API_KEY` (from each app's
Settings → General → API Key). Leave `DRY_RUN=true` for now.

Install the service and timer:

```bash
sudo cp ~/Documents/Gits/server-stack/services/queue-cleaner.service /etc/systemd/system/
sudo cp ~/Documents/Gits/server-stack/services/queue-cleaner.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now queue-cleaner.timer
```

## Verification

Force an immediate run and tail the logs:

```bash
sudo systemctl start queue-cleaner.service
sudo journalctl -u queue-cleaner -n 100 --no-pager
```

In dry-run mode you should see one of:

- `DRY   [sonarr] stall id=...` — Mode 1 match
- `DRY   [sonarr] import_fail id=...` — Mode 2 match
- `DRY   [sonarr] force_upgrade id=...` — Mode 3 match
- `INFO  [sonarr] N queue item(s) returned` followed by `DEBUG skip` lines
  explaining why each item did not match, then `would act on 0 item(s)`

If the matched items look right, flip out of dry-run mode:

```bash
sudo sed -i 's/^DRY_RUN=true/DRY_RUN=false/' /etc/queue-cleaner.env
sudo systemctl start queue-cleaner.service
sudo journalctl -u queue-cleaner -n 50 --no-pager
```

Check the timer is scheduled:

```bash
sudo systemctl list-timers queue-cleaner.timer
```

## Service management

```bash
# Status of the timer (when it last ran, when it runs next)
sudo systemctl status queue-cleaner.timer

# Force a run now
sudo systemctl start queue-cleaner.service

# Stop scheduled runs
sudo systemctl disable --now queue-cleaner.timer

# View recent logs
sudo journalctl -u queue-cleaner -n 100 --no-pager
```

## Configuration reference

All settings live in `/etc/queue-cleaner.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SONARR_URL` | (unset) | Sonarr base URL. Leave blank with `SONARR_API_KEY` to skip Sonarr. |
| `SONARR_API_KEY` | (unset) | Sonarr API key. Get from Settings → General → API Key. |
| `RADARR_URL` | (unset) | Radarr base URL. Leave blank with `RADARR_API_KEY` to skip Radarr. |
| `RADARR_API_KEY` | (unset) | Radarr API key. |
| `DRY_RUN` | `true` | If true, log matches but do not act. Always start here. |
| `BLOCKLIST` | `true` | If true, Mode 1/2 deletes use `blocklist=true`. |
| `MIN_AGE_MINUTES` | `15` | Skip items added within the last N minutes. |
| `ERROR_PATTERNS` | see env example | Mode 1: comma-separated substrings matched against `errorMessage`. Item must also have `size==0` and `sizeleft==0`. |
| `IMPORT_FAIL_PATTERNS` | see env example | Mode 2: comma-separated substrings, no size check. Set to empty string to disable. |
| `FORCE_UPGRADE` | `true` | If true, enable Mode 3 force-upgrade. |
| `FORCE_UPGRADE_PATTERNS` | see env example | Mode 3: comma-separated substrings matched against `errorMessage`. |

## Troubleshooting

### `would act on 0 item(s)` but I have stuck items

Run with the debug skip lines visible to see which check is failing:

```bash
sudo systemctl start queue-cleaner.service
sudo journalctl -u queue-cleaner -n 200 --no-pager | grep -i "skip\|matched\|candidate"
```

Common reasons:

- `age=X.Xm < 15m`: item is younger than `MIN_AGE_MINUTES`. Wait, or lower
  the threshold.
- `errorMessage='...' does not match any pattern`: add the string to the
  appropriate pattern list in `/etc/queue-cleaner.env`.
- `stall candidate but size=N != 0`: item has a real file size so Mode 1
  won't touch it. If it's an import failure, check `IMPORT_FAIL_PATTERNS`.

### Re-grab loop after Mode 1/2 delete

Confirm Prowlarr's **Sync Reject Blocklisted Torrent Hashes While Grabbing**
is enabled on both the Sonarr and Radarr app entries. Without it, the
blocklist only blocks the specific indexer the original grab came from.

### Force upgrade imported a worse file

Set `FORCE_UPGRADE=false` in `/etc/queue-cleaner.env` to disable Mode 3.
Manually restore from your backup or re-grab the preferred quality.

### Wrong API key / connection refused

```bash
# Confirm keys are loaded into the service environment
sudo systemctl show queue-cleaner.service -p Environment

# Smoke-test the Sonarr API by hand
curl -s -H "X-Api-Key: $(grep ^SONARR_API_KEY /etc/queue-cleaner.env | cut -d= -f2)" \
  http://localhost:8989/api/v3/system/status | python3 -m json.tool | head -5
```

---

## Docker install

In the Docker Compose lab target, queue-cleaner runs as a container in
the `indexers` profile. See `compose/docker-compose.yml` for the full
service definition.

The native install uses a systemd timer (`queue-cleaner.timer`) to run
the script every 5 minutes. In Docker there is no systemd, so the
container loops with a `sleep 300` between runs.

```bash
# From repo root on media-vm
docker compose -f compose/docker-compose.yml --profile indexers up -d queue-cleaner
```

The container is built from `docker/queue-cleaner/Dockerfile`. API keys
and settings go in a `queue-cleaner.env` file (copy from
`docker/queue-cleaner/example.env`) and referenced via `QUEUE_CLEANER_ENV`
in `compose/.env`.

Note: inside the Docker network, `SONARR_URL` and `RADARR_URL` use
container names (`http://sonarr:8989`, `http://radarr:7878`) not
`localhost`. See `docker/queue-cleaner/example.env`.
