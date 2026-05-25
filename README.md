# server-stack

Self-hosted server stack on Debian 12. Covers media automation and network
utilities.

## Media

| Service | Purpose | Port | Docs |
| --- | --- | --- | --- |
| Radarr | Movie management | 7878 | [Install & Config](docs/radarr.md) |
| Sonarr | TV show management | 8989 | [Install & Config](docs/sonarr.md) |
| Prowlarr | Indexer management | 9696 | [Install & Config](docs/prowlarr.md) |
| rdt-client | Real-Debrid download client | 6500 | [Install & Config](docs/rdt-client.md) |
| FlareSolverr | Cloudflare bypass proxy | 8191 | [Install & Config](docs/flaresolverr.md) |
| Decluttarr | Queue cleanup for failed/stalled downloads | - | [Install & Config](docs/decluttarr.md) |
| queue-cleaner | Sidecar for stuck rdt-client 500 queue items | - | [Install & Config](docs/queue-cleaner.md) |
| ClamAV | Malware scanning | - | [Install & Config](docs/clamav.md) |

Plex, Overseerr, and Tautulli are assumed to already be installed and are not
managed by this stack.

### How it works

```
Overseerr -> Radarr/Sonarr -> Prowlarr -> rdt-client -> Real-Debrid -> local download -> Plex
```

1. A user requests content in Overseerr
2. Overseerr sends the request to Radarr (movies) or Sonarr (TV)
3. Radarr/Sonarr asks Prowlarr to search configured indexers for a matching torrent
4. The torrent is sent to rdt-client which pushes it to Real-Debrid
5. Real-Debrid debrids the torrent and serves a direct download link
6. rdt-client downloads the file to the local downloads directory
7. Radarr/Sonarr detects the completed download, renames and moves it to the media library
8. Plex picks up the new file automatically

Decluttarr runs alongside this pipeline. If Real-Debrid rejects a torrent
(for example as an infringing hash), rdt-client returns a 500 that Sonarr
and Radarr mistake for a transient connection error, leaving the item stuck
in the queue. Decluttarr detects the stall, blocklists the release, and
re-triggers the search so the pipeline can move on.

queue-cleaner is a small purpose-built sidecar that targets one specific
variant decluttarr does not catch out of the box: queue items left with
`status=warning` and `errorMessage="qBittorrent is reporting an error"`.
It runs every 5 minutes via a systemd timer and deletes + blocklists those
items. See [queue-cleaner docs](docs/queue-cleaner.md) for the full rationale.

### Requirements

- Debian 12 (Bookworm)
- An active Real-Debrid premium subscription
- Plex, Overseerr, and Tautulli already installed and running
- sudo access

### Installation

This stack is installed manually. Follow the docs in order:

1. [Permissions](docs/permissions.md) - set up the media user and directory structure first
2. [rdt-client](docs/rdt-client.md) - install the download client
3. [Radarr](docs/radarr.md) - install the movie manager
4. [Sonarr](docs/sonarr.md) - install the TV show manager
5. [Prowlarr](docs/prowlarr.md) - install the indexer manager and connect it to Radarr/Sonarr
6. [Decluttarr](docs/decluttarr.md) - install queue cleanup for failed/stalled downloads
7. [queue-cleaner](docs/queue-cleaner.md) - install the rdt-client 500 sidecar
8. [FlareSolverr](docs/flaresolverr.md) - install the Cloudflare bypass proxy (optional)
9. [ClamAV](docs/clamav.md) - install malware scanning (optional)

### File structure

```
/mnt/raid/media/
    Videos/
        Movies/         <- Radarr root folder
        TV Shows/       <- Sonarr root folder
    Downloads/
        radarr/         <- Radarr category download folder
        sonarr/         <- Sonarr category download folder
        rdtclient.db    <- rdt-client database
        rdtclient.log   <- rdt-client logs

/opt/
    Radarr/             <- Radarr binaries
    Sonarr/             <- Sonarr binaries
    Prowlarr/           <- Prowlarr binaries
    rdt-client/         <- rdt-client binaries
    decluttarr/         <- Decluttarr source and venv
        config/
            config.yaml <- Decluttarr config
    queue-cleaner/
        queue-cleaner.py <- queue-cleaner script

/etc/
    queue-cleaner.env   <- queue-cleaner API keys and settings (root:root 600)

/var/lib/
    radarr/             <- Radarr config and database
    sonarr/             <- Sonarr config and database
    prowlarr/           <- Prowlarr config and database
```

### Permissions

All services run as a single `media` user. Plex and your personal user are added
to the `media` group for read access to the library. See [permissions](docs/permissions.md)
for full details.

### Service management

```bash
# Check status
sudo systemctl status radarr sonarr prowlarr rdt-client decluttarr queue-cleaner.timer

# Restart a service
sudo systemctl restart radarr

# View logs
sudo journalctl -u radarr -n 50
```

### Known issues

- rdt-client creates a subfolder per download (e.g. Show.S01E01.mkv/Show.S01E01.mkv).
  Running all services as the same media user means Sonarr and Radarr can read these
  nested folders directly without any issues.
- FlareSolverr effectiveness depends on Chromium version. See
  [FlareSolverr docs](docs/flaresolverr.md) for details.
- Sonarr can show No indexers available if Prowlarr sync fails. See
  [Prowlarr troubleshooting](docs/prowlarr.md) for details.

## Network

| Service | Purpose | Docs |
| --- | --- | --- |
| DNS Updater | Dynamic DNS updater for DreamHost via systemd timer | [Install & Config](docs/dns-updater.md) |
