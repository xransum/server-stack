# Decluttarr

Queue cleanup for the arr stack. Decluttarr monitors the Sonarr and Radarr
download queues on a timer, removes failed and stalled downloads, blocklists
the offending release, and triggers a new search.

This service exists in this stack specifically to work around a Real-Debrid
edge case: when Real-Debrid rejects a torrent hash as an infringing file,
rdt-client returns a `500` to Sonarr instead of a proper failure. Sonarr
interprets that as a transient connection error rather than a failed download,
so it never blocklists the release or retries. Decluttarr watches the queue
independently and cleans these up.

- **Port**: none (outbound API client only)
- **Runs as**: `media` system user
- **Install path**: `/opt/decluttarr`
- **Config**: `/opt/decluttarr/config/config.yaml`

## Prerequisites

- Python 3, `python3-venv`, and `git`
- Sonarr running and reachable at `http://localhost:8989`
- Radarr running and reachable at `http://localhost:7878`
- rdt-client running and reachable at `http://localhost:6500`
- The `media` user must exist. See [permissions](permissions.md).

## Install

Install system dependencies:

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
```

Clone the repository:

```bash
sudo git clone -b latest https://github.com/ManiMatter/decluttarr.git /opt/decluttarr
sudo chown -R media:media /opt/decluttarr
```

Create the virtual environment and install Python dependencies as the
`media` user:

```bash
sudo -u media python3 -m venv /opt/decluttarr/.venv
sudo -u media /opt/decluttarr/.venv/bin/pip install --upgrade pip
sudo -u media /opt/decluttarr/.venv/bin/pip install -r /opt/decluttarr/docker/requirements.txt
```

The upstream repo ships an empty `config/` directory at `/opt/decluttarr/config`
which is where the runtime config below lives.

## Configuration

Get a Sonarr API key from Sonarr -> Settings -> General -> API Key and a
Radarr API key from Radarr -> Settings -> General -> API Key. Also have your
rdt-client login credentials handy.

Create `/opt/decluttarr/config/config.yaml`:

```yaml
# Run in test mode first. Decluttarr will log what it WOULD do without
# actually removing or blocklisting anything. Flip to False after verifying
# behavior in the logs.
test_run: True

general:
  log_level: INFO
  # How often (in minutes) to scan the queue
  timer: 10

jobs:
  # Remove downloads that failed to download (the case rdt-client 500s hit).
  # Blocklists the release in Sonarr and triggers a fresh search.
  remove_failed_downloads: True

  # Remove downloads that completed but failed to import into the library.
  remove_failed_imports: True

  # Remove downloads that are stalled (no progress) after the configured
  # number of strikes.
  remove_stalled: True

  # Intentionally disabled in this stack. Enable only if you know you want them.
  remove_slow: False
  remove_unmonitored: False
  remove_orphans: False

# rdt-client exposes a qBittorrent-compatible API on port 6500.
download_clients:
  qbittorrent:
    - name: rdt-client
      url: http://localhost:6500
      username: YOUR_RDTCLIENT_USERNAME
      password: YOUR_RDTCLIENT_PASSWORD

instances:
  sonarr:
    - name: sonarr
      url: http://localhost:8989
      api_key: YOUR_SONARR_API_KEY

  radarr:
    - name: radarr
      url: http://localhost:7878
      api_key: YOUR_RADARR_API_KEY
```

```bash
sudo chown media:media /opt/decluttarr/config/config.yaml
sudo chmod 640 /opt/decluttarr/config/config.yaml
```

## Required Prowlarr setting

In Prowlarr, go to Settings -> Apps -> **Sonarr**, click **Show Advanced**,
and enable **Reject Blocklisted Torrent Hashes While Grabbing**. Repeat for
the **Radarr** entry under the same Apps page.

Without this, the blocklist will not prevent the same torrent hash from being
grabbed again from a different indexer, and decluttarr's blocklist action
will get undone on the next search.

## Service file

Copy the service file from the repo and enable it:

```bash
sudo cp services/decluttarr.service /etc/systemd/system/decluttarr.service
sudo systemctl daemon-reload
sudo systemctl enable --now decluttarr
```

## Verification

Tail the logs through one full cycle:

```bash
sudo journalctl -u decluttarr -f
```

You should see decluttarr successfully connect to the Sonarr instance, the
Radarr instance, and the qBittorrent (rdt-client) endpoint, then iterate
each queue. With `test_run: True`, any actions it would take are logged but
not executed.

Once the connections succeed and the intended actions look correct, switch
`test_run` to `False` in `/opt/decluttarr/config/config.yaml` and restart:

```bash
sudo systemctl restart decluttarr
```

## Service Management

```bash
sudo systemctl status decluttarr
sudo systemctl restart decluttarr
sudo journalctl -u decluttarr -n 50
```

## Updating

```bash
sudo systemctl stop decluttarr
sudo -u media git -C /opt/decluttarr pull
sudo -u media /opt/decluttarr/.venv/bin/pip install -r /opt/decluttarr/docker/requirements.txt
sudo systemctl start decluttarr
```

Your `config/config.yaml` is preserved across updates (it is not tracked by
the upstream repo).

## Troubleshooting

### Auth failures against rdt-client

Decluttarr talks to rdt-client through its qBittorrent API shim. The username
and password in `config.yaml` are the **rdt-client login credentials** you
created on first visit to `http://localhost:6500`, not Real-Debrid credentials.

### Same release gets re-grabbed after blocklisting

Confirm the Prowlarr setting above (**Reject Blocklisted Torrent Hashes While
Grabbing**) is enabled for both the Sonarr and Radarr app entries. Without
it the blocklist only prevents re-grabs from the same indexer.

### Decluttarr never takes action

If the logs show queue items being inspected but nothing is ever removed,
check that `test_run` is set to `False` in `config.yaml` and the service has
been restarted.
