# queue-cleaner

A small sidecar that polls the Sonarr and Radarr download queues on a timer
and removes items stuck in a specific failure state that decluttarr does not
catch.

- **Port**: none (outbound API client only)
- **Runs as**: `media` system user
- **Install path**: `/opt/queue-cleaner/`
- **Config**: `/etc/queue-cleaner.env`
- **Schedule**: every 5 minutes via systemd timer

## Why this exists

The stack already runs [decluttarr](decluttarr.md) for queue cleanup, but
decluttarr's stock jobs do not match the exact failure mode produced by
Real-Debrid rejecting an infringing torrent hash:

- `remove_failed_downloads` only acts on `status == "failed"`. Sonarr/Radarr
  mark these items as `status == "warning"`.
- `remove_stalled` only acts on the literal `errorMessage` string
  `"The download is stalled with no connections"`. The actual message we see
  for rdt-client 500s is `"qBittorrent is reporting an error"`.

So these items sit in the queue forever. A real example pulled from the
Sonarr API for a stuck Book of Boba Fett release:

```json
{
  "status": "warning",
  "trackedDownloadStatus": "ok",
  "trackedDownloadState": "downloading",
  "errorMessage": "qBittorrent is reporting an error",
  "size": 0,
  "sizeleft": 0,
  "timeleft": "00:00:00",
  "downloadClient": "qBittorrent"
}
```

queue-cleaner watches for exactly this shape (status, error message, no
bytes, sufficient age) and deletes those items with `removeFromClient=true`
and `blocklist=true`. decluttarr stays installed for everything else
(`remove_failed_imports`, `remove_stalled` proper, strikes, etc.).

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
Settings -> General -> API Key). Leave `DRY_RUN=true` for now.

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

- `DRY   [sonarr] delete id=... title=...` lines for any matching stuck items, or
- `INFO  [sonarr] N queue item(s) returned` followed by `DEBUG` skip lines
  explaining why each item did not match, then `would delete 0 item(s)`.

If the matched items look right, flip out of dry-run mode:

```bash
sudo sed -i 's/^DRY_RUN=true/DRY_RUN=false/' /etc/queue-cleaner.env
sudo systemctl start queue-cleaner.service
sudo journalctl -u queue-cleaner -n 50 --no-pager
```

Subsequent timer-driven runs will now actually delete + blocklist.

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
| `SONARR_API_KEY` | (unset) | Sonarr API key. |
| `RADARR_URL` | (unset) | Radarr base URL. Leave blank with `RADARR_API_KEY` to skip Radarr. |
| `RADARR_API_KEY` | (unset) | Radarr API key. |
| `DRY_RUN` | `true` | If true, log matches but do not delete. |
| `BLOCKLIST` | `true` | If true, delete with `blocklist=true`. |
| `MIN_AGE_MINUTES` | `15` | Skip items added within the last N minutes. |
| `ERROR_PATTERNS` | see env example | Comma-separated case-insensitive substrings matched against `errorMessage`. |

## Troubleshooting

### `would delete 0 item(s)` but I have stuck items

Run with the debug skip lines visible to see which check is failing:

```bash
sudo systemctl start queue-cleaner.service
sudo journalctl -u queue-cleaner -n 200 --no-pager | grep -i "skip\|matched"
```

The most common reasons:

- `age=X.Xm < 15m`: item is younger than `MIN_AGE_MINUTES`. Wait, or lower
  the threshold.
- `errorMessage='...' does not match patterns`: rdt-client is producing a
  new error string. Add it to `ERROR_PATTERNS` in `/etc/queue-cleaner.env`.
- `size=N != 0` or `sizeleft=N != 0`: the item is reporting non-zero bytes,
  so it is not the stuck-with-zero-progress signature this script targets.
  Let decluttarr or a manual review handle it.

### Re-grab loop after delete

Confirm Prowlarr's **Sync Reject Blocklisted Torrent Hashes While Grabbing**
is enabled on both the Sonarr and Radarr app entries. Without it, the
blocklist only blocks the specific indexer the original grab came from. See
[decluttarr docs](decluttarr.md#required-prowlarr-setting).

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
container loops with a `sleep 300` between runs. This is noted in the
compose file and can be replaced with a proper cron container if preferred.

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
