# rdt-blocklist

A long-running sidecar that watches the rdt-client SQLite database for
torrents stuck with a Real-Debrid provider error, finds the matching item in
the Sonarr and/or Radarr download queue, blocklists it, and triggers a fresh
search — without any human intervention.

- **Port**: none (outbound API client only)
- **Runs as**: `media` system user
- **Install path**: `/opt/rdt-blocklist/`
- **Config**: `/etc/rdt-blocklist.env`
- **Mode**: persistent service (polls every 2 minutes)

## Why this exists

When Real-Debrid rejects a torrent hash as infringing, rdt-client records the
error in its local SQLite database but never propagates a failure signal back
to Sonarr or Radarr. From their perspective the download client (rdt-client
posing as qBittorrent) shows `status=warning, trackedDownloadStatus=ok` — the
item sits in the queue forever and no alternative release is ever grabbed.

A real example pulled live from the rdt-client database:

```
TorrentId: 4EA0A80D-54EB-442C-AD85-412BC0AF01B6
RdName:    Widows.Bay.S01E01.MULTI.1080p.WEB.H264-HiggsBoson
Error:     Could not add to provider: Infringing file
```

Meanwhile Sonarr showed `status=downloading, errorMessage=""` for the same
release — it had no idea anything was wrong.

### Why not queue-cleaner?

[queue-cleaner](queue-cleaner.md) catches a related but different failure mode:
items where `status=warning` + `errorMessage="qBittorrent is reporting an
error"` + `size=0`. That fires when rdt-client returns a 500 after the
provider error has already been retried and the item has surfaced to the queue
layer. rdt-blocklist fires earlier, directly against the database error, and
also cleans up the stuck DB row so rdt-client stops retrying it. Both scripts
can run simultaneously — they target different stages of the same failure.

### Why not Decluttarr?

Decluttarr's `remove_failed_downloads` only acts on `status == "failed"`.
`remove_stalled` only matches the literal string `"The download is stalled
with no connections"`. Neither matches the infringing pattern.

## Prerequisites

- Python 3 (system `/usr/bin/python3`, stdlib only)
- rdt-client running with its database at the configured path
- Sonarr and/or Radarr reachable
- The `media` user must exist. See [permissions](permissions.md).

## Install

Pull the repo (if you haven't already):

```bash
git -C ~/Documents/Gits/server-stack pull
```

Drop the script into `/opt/rdt-blocklist/`:

```bash
sudo mkdir -p /opt/rdt-blocklist
sudo cp ~/Documents/Gits/server-stack/scripts/rdt-blocklist.py /opt/rdt-blocklist/
sudo chown -R media:media /opt/rdt-blocklist
sudo chmod 755 /opt/rdt-blocklist/rdt-blocklist.py
```

Install the environment file:

```bash
sudo cp ~/Documents/Gits/server-stack/scripts/rdt-blocklist.env.example /etc/rdt-blocklist.env
sudo chown root:root /etc/rdt-blocklist.env
sudo chmod 600 /etc/rdt-blocklist.env
sudo nano /etc/rdt-blocklist.env
```

Fill in `SONARR_API_KEY` and `RADARR_API_KEY`. Leave `DRY_RUN=true` for now.

Install and enable the service:

```bash
sudo cp ~/Documents/Gits/server-stack/services/rdt-blocklist.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rdt-blocklist
```

## Verification

Watch the logs live:

```bash
sudo journalctl -u rdt-blocklist -f --no-pager
```

On startup you should see:

```
INFO  rdt-blocklist start dry_run=True one_shot=False poll_interval=120s ...
```

If there are currently stuck torrents, you will see `DRY` lines showing what
would be blocklisted. When the output looks correct, disable dry-run:

```bash
sudo sed -i 's/^DRY_RUN=true/DRY_RUN=false/' /etc/rdt-blocklist.env
sudo systemctl restart rdt-blocklist
```

## Service management

```bash
# Status
sudo systemctl status rdt-blocklist

# View logs
sudo journalctl -u rdt-blocklist -n 100 --no-pager

# Restart (e.g. after config change)
sudo systemctl restart rdt-blocklist

# Stop
sudo systemctl stop rdt-blocklist
```

## Configuration reference

All settings live in `/etc/rdt-blocklist.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RDT_DB_PATH` | (required) | Absolute path to the rdt-client SQLite database. |
| `SONARR_URL` | (unset) | Sonarr base URL. Leave blank to skip Sonarr. |
| `SONARR_API_KEY` | (unset) | Sonarr API key. |
| `RADARR_URL` | (unset) | Radarr base URL. Leave blank to skip Radarr. |
| `RADARR_API_KEY` | (unset) | Radarr API key. |
| `DRY_RUN` | `true` | If true, log matches but do not blocklist or delete. |
| `POLL_INTERVAL_SECONDS` | `120` | How often to poll the database. |
| `ERROR_PATTERNS` | see env example | Comma-separated substrings matched against the `Error` column. |

## Troubleshooting

### Torrent matched but wrong queue item blocklisted

The title matching is a loose substring match — it checks whether the RDT
torrent name contains the Sonarr/Radarr queue title or vice versa. If two
releases with similar names are in the queue simultaneously this could
mismatch. In practice this is very rare; the `DEBUG` log lines show exactly
which pair was matched.

### `RDT_DB_PATH` not set

```
ERROR RDT_DB_PATH is not set
```

The service will exit immediately. Add `RDT_DB_PATH=` to `/etc/rdt-blocklist.env`
and restart.

### Database locked / permission denied

The database is owned by the `media` user on serverhub. The service runs as
`media` so this should not occur on a correct install. If you see it:

```bash
ls -la /mnt/raid0/media/Downloads/rdtclient.db
# Should be: -rw-r--r-- 1 media media ...
```

### Re-grab loop after blocklist

Confirm Prowlarr's **Sync Reject Blocklisted Torrent Hashes While Grabbing**
is enabled on both the Sonarr and Radarr app entries. Without it the blocklist
only blocks the specific indexer the original grab came from. See
[decluttarr docs](decluttarr.md#required-prowlarr-setting).

---

## Docker install

In the Docker Compose lab target, rdt-blocklist runs as a container in the
`indexers` profile alongside queue-cleaner, rdt-client, Sonarr, and Radarr.

```bash
# From repo root on media-vm
docker compose -f compose/docker-compose.yml --profile indexers up -d rdt-blocklist
```

The container is built from `docker/rdt-blocklist/Dockerfile`. The rdt-client
database must be accessible inside the container — it is mounted read-only
from the same NFS share as downloads (`/mnt/media/downloads`).

API keys and settings go in an `rdt-blocklist.env` file (copy from
`docker/rdt-blocklist/example.env`) and referenced via `RDT_BLOCKLIST_ENV` in
`compose/.env`.

Note: inside the Docker network, `SONARR_URL` and `RADARR_URL` use container
names (`http://sonarr:8989`, `http://radarr:7878`) not `localhost`. See
`docker/rdt-blocklist/example.env`.
