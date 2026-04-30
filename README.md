# media-stack

Self-hosted media automation stack on Debian 12 using Real-Debrid.

## Stack

| Service | Purpose | Port | Docs |
| --- | --- | --- | --- |
| Radarr | Movie management | 7878 | [Install & Config](docs/radarr.md) |
| Sonarr | TV show management | 8989 | [Install & Config](docs/sonarr.md) |
| Prowlarr | Indexer management | 9696 | [Install & Config](docs/prowlarr.md) |
| rdt-client | Real-Debrid download client | 6500 | [Install & Config](docs/rdt-client.md) |
| FlareSolverr | Cloudflare bypass proxy | 8191 | [Install & Config](docs/flaresolverr.md) |
| ClamAV | Malware scanning | - | [Install & Config](docs/clamav.md) |

Plex, Overseerr, and Tautulli are assumed to already be installed and are not
managed by this stack.

## How it works

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

## Requirements

- Debian 12 (Bookworm)
- An active Real-Debrid premium subscription
- Plex, Overseerr, and Tautulli already installed and running
- sudo access

## Installation

This stack is installed manually. Follow the docs in order:

1. [Permissions](docs/permissions.md) - set up the media user and directory structure first
2. [rdt-client](docs/rdt-client.md) - install the download client
3. [Radarr](docs/radarr.md) - install the movie manager
4. [Sonarr](docs/sonarr.md) - install the TV show manager
5. [Prowlarr](docs/prowlarr.md) - install the indexer manager and connect it to Radarr/Sonarr
6. [FlareSolverr](docs/flaresolverr.md) - install the Cloudflare bypass proxy (optional)
7. [ClamAV](docs/clamav.md) - install malware scanning (optional)

## File structure

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

/var/lib/
    radarr/             <- Radarr config and database
    sonarr/             <- Sonarr config and database
    prowlarr/           <- Prowlarr config and database
```

## Permissions

All services run as a single `media` user. Plex and your personal user are added
to the `media` group for read access to the library. See [permissions](docs/permissions.md)
for full details.

## Service management

```bash
# Check status
sudo systemctl status radarr sonarr prowlarr rdt-client

# Restart a service
sudo systemctl restart radarr

# View logs
sudo journalctl -u radarr -n 50
```

## Known issues

- rdt-client creates a subfolder per download (e.g. Show.S01E01.mkv/Show.S01E01.mkv).
  Running all services as the same media user means Sonarr and Radarr can read these
  nested folders directly without any issues.
- FlareSolverr effectiveness depends on Chromium version. See
  [FlareSolverr docs](docs/flaresolverr.md) for details.
- Sonarr can show No indexers available if Prowlarr sync fails. See
  [Prowlarr troubleshooting](docs/prowlarr.md) for details.
