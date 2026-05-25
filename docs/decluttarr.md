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

Create the virtual environment and install Python dependencies. We use the
absolute path `/usr/bin/python3` to avoid picking up any user-level Python
manager (pyenv, asdf, conda) that might leak through `sudo`. The venv is
created as root and then handed to `media` so the systemd service can use
it without depending on any user's shell environment:

```bash
sudo /usr/bin/python3 -m venv /opt/decluttarr/.venv
sudo /opt/decluttarr/.venv/bin/pip install --upgrade pip
sudo /opt/decluttarr/.venv/bin/pip install -r /opt/decluttarr/docker/requirements.txt
sudo chown -R media:media /opt/decluttarr/.venv
```

Verify the venv is linked to the system Python (not a pyenv/asdf shim):

```bash
readlink -f /opt/decluttarr/.venv/bin/python
# Expected: /usr/bin/python3.x  (NOT anything under /home or ~/.pyenv)
```

The upstream repo's `config/` directory is not reliably created by `git
clone` (git does not track empty directories), so create it explicitly and
hand it to the `media` user:

```bash
sudo mkdir -p /opt/decluttarr/config
sudo chown media:media /opt/decluttarr/config
```

## Configuration

Get a Sonarr API key from Sonarr -> Settings -> General -> API Key and a
Radarr API key from Radarr -> Settings -> General -> API Key. Also have your
rdt-client login credentials handy.

Create `/opt/decluttarr/config/config.yaml` (edit as root since the `media`
user has no login shell):

```bash
sudoedit /opt/decluttarr/config/config.yaml
# or: sudo nano /opt/decluttarr/config/config.yaml
```

Paste:

```yaml
general:
  log_level: INFO
  # Run in test mode first. Decluttarr will log what it WOULD do without
  # actually removing or blocklisting anything. Flip to false after verifying
  # behavior in the logs.
  test_run: true
  # How often (in minutes) to scan the queue
  timer: 10

job_defaults:
  max_strikes: 3
  min_days_between_searches: 7
  max_concurrent_searches: 3

# Jobs are enabled by including their key. Omit a job to disable it.
# Slow / unmonitored / orphan jobs are intentionally left out for this stack.
jobs:
  # Remove downloads that failed to download (the case rdt-client 500s hit).
  # Blocklists the release and triggers a fresh search.
  remove_failed_downloads:

  # Remove downloads that completed but failed to import into the library.
  # message_patterns matches Sonarr/Radarr's reported import failure reason;
  # this list is the upstream-recommended default and only acts on known-bad
  # cases (avoids touching transient permission/network errors).
  remove_failed_imports:
    message_patterns:
      - "Not a Custom Format upgrade for existing*"
      - "Not an upgrade for existing*"
      - "*Found potentially dangerous file with extension*"
      - "Invalid video file*"
      - "No files found are eligible for import*"
      - "One or more episodes expected in this release were not imported or missing from the release"

  # Remove downloads that are stalled (no progress) after max_strikes cycles.
  remove_stalled:

instances:
  sonarr:
    - base_url: "http://localhost:8989"
      api_key: "YOUR_SONARR_API_KEY"

  radarr:
    - base_url: "http://localhost:7878"
      api_key: "YOUR_RADARR_API_KEY"

# rdt-client exposes a qBittorrent-compatible API on port 6500.
# `name` defaults to "qBittorrent", which matches the default name Sonarr and
# Radarr assign when you add a qBittorrent download client. If you renamed
# the download client in Sonarr/Radarr, set `name:` here to match.
download_clients:
  qbittorrent:
    - base_url: "http://localhost:6500"
      username: "YOUR_RDTCLIENT_USERNAME"
      password: "YOUR_RDTCLIENT_PASSWORD"
```

```bash
sudo chown media:media /opt/decluttarr/config/config.yaml
sudo chmod 640 /opt/decluttarr/config/config.yaml
```

## Required Prowlarr setting

In Prowlarr, go to Settings -> Apps -> **Sonarr**, click **Show Advanced**,
and enable **Sync Reject Blocklisted Torrent Hashes While Grabbing**. Repeat
for
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
each queue. With `test_run: true`, any actions it would take are logged but
not executed.

Once the connections succeed and the intended actions look correct, switch
`test_run` to `false` under `general:` in `/opt/decluttarr/config/config.yaml`
and restart:

```bash
sudo sed -i 's/^  test_run: true/  test_run: false/' /opt/decluttarr/config/config.yaml
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
sudo git -C /opt/decluttarr pull
sudo /opt/decluttarr/.venv/bin/pip install -r /opt/decluttarr/docker/requirements.txt
sudo chown -R media:media /opt/decluttarr
sudo systemctl start decluttarr
```

Your `config/config.yaml` is preserved across updates (it is not tracked by
the upstream repo).

## Limitations (and what queue-cleaner covers)

Decluttarr's stock jobs match on specific status / errorMessage values:

- `remove_failed_downloads` requires `status == "failed"`.
- `remove_stalled` requires the literal `errorMessage == "The download is
  stalled with no connections"`.

The rdt-client 500-on-infringing-hash failure we installed decluttarr for
actually shows up in Sonarr/Radarr as `status == "warning"` with
`errorMessage == "qBittorrent is reporting an error"`, which neither job
matches. That specific case is handled by the
[queue-cleaner](queue-cleaner.md) sidecar instead. Decluttarr still earns
its keep for failed imports, true stalls, and strike-based recovery.

## Troubleshooting

### Auth failures against rdt-client

Decluttarr talks to rdt-client through its qBittorrent API shim. The username
and password in `config.yaml` are the **rdt-client login credentials** you
created on first visit to `http://localhost:6500`, not Real-Debrid credentials.

### Same release gets re-grabbed after blocklisting

Confirm the Prowlarr setting above (**Sync Reject Blocklisted Torrent Hashes
While
Grabbing**) is enabled for both the Sonarr and Radarr app entries. Without
it the blocklist only prevents re-grabs from the same indexer.

### Decluttarr never takes action

If the logs show queue items being inspected but nothing is ever removed,
check that `test_run` is set to `false` under `general:` in `config.yaml` and
the service has
been restarted.
