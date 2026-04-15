# media-stack

Self-hosted media automation stack on Debian 12 using Real-Debrid.

## Stack

| Service | Purpose | Port | Docs |
|---|---|---|---|
| [Radarr](docs/radarr.md) | Movie management | 7878 | [Install & Config](docs/radarr.md) |
| [Sonarr](docs/sonarr.md) | TV show management | 8989 | [Install & Config](docs/sonarr.md) |
| [Prowlarr](docs/prowlarr.md) | Indexer management | 9696 | [Install & Config](docs/prowlarr.md) |
| [rdt-client](docs/rdt-client.md) | Real-Debrid download client | 6500 | [Install & Config](docs/rdt-client.md) |
| [FlareSolverr](docs/flaresolverr.md) | Cloudflare bypass proxy | 8191 | [Install & Config](docs/flaresolverr.md) |
| [ClamAV](docs/clamav.md) | Malware scanning | - | [Install & Config](docs/clamav.md) |
| [Flatten Downloads](docs/flatten-downloads.md) | Auto-flatten nested mkv/mp4 folders | - | [Install & Config](docs/flatten-downloads.md) |
| [Notifications](docs/notifications.md) | Discord webhook alerts | - | [Setup Guide](docs/notifications.md) |

Plex, Overseerr, and Tautulli are assumed to already be installed and are not managed by this stack.

## How It Works

```text
Overseerr -> Radarr/Sonarr -> Prowlarr -> rdt-client -> Real-Debrid -> local download -> Plex
```

1. A user requests a movie or show in Overseerr
2. Overseerr sends the request to Radarr (movies) or Sonarr (TV)
3. Radarr/Sonarr asks Prowlarr to search configured indexers for a matching torrent
4. The torrent is sent to rdt-client, which pushes it to Real-Debrid
5. Real-Debrid debrids the torrent and serves a direct download link
6. rdt-client downloads the file to the local downloads directory
7. Radarr/Sonarr detects the completed download, renames and moves it to the media library
8. Plex picks up the new file automatically

## Requirements

- Debian 12 (Bookworm)
- An active Real-Debrid premium subscription
- Plex, Overseerr, and Tautulli already installed and running
- `pyenv` and `pyenv-virtualenv` installed for the user running `sudo` (for FlareSolverr)
- `sudo` access
- `ufw` firewall active

## File Structure

```text
/mnt/raid/media/
    Videos/
        Movies/         <- Radarr root folder
        TV Shows/       <- Sonarr root folder
    Downloads/          <- rdt-client download path
        radarr/         <- Radarr category subfolder
        sonarr/         <- Sonarr category subfolder
        rdtclient.db    <- rdt-client database
        rdtclient.log   <- rdt-client logs

/opt/
    Radarr/             <- Radarr binaries
    Sonarr/             <- Sonarr binaries
    Prowlarr/           <- Prowlarr binaries
    rdt-client/         <- rdt-client binaries

/var/lib/
    radarr/             <- Radarr config/database
    sonarr/             <- Sonarr config/database
    prowlarr/           <- Prowlarr config/database

~/.pyenv/versions/
    flaresolverr-env/   <- FlareSolverr Python 3.11 virtualenv
```

## Quick Start (Automated)

Edit the config variables at the top of `install.sh` to match your paths and media group, then:

```bash
chmod +x install.sh
sudo ./install.sh
```

The script installs everything, creates systemd services, sets up the `mediadl` shared group, and configures permissions. All services start automatically on boot.

After the script finishes, follow the post-install configuration steps for each service (linked in the docs above).

## Manual Install

Each service can be installed independently. See the individual docs for step-by-step manual instructions:

1. [Permissions](docs/permissions.md) - set up the `mediadl` group and directory structure first
2. [rdt-client](docs/rdt-client.md) - install the download client
3. [Radarr](docs/radarr.md) - install the movie manager
4. [Sonarr](docs/sonarr.md) - install the TV show manager
5. [Prowlarr](docs/prowlarr.md) - install the indexer manager and connect it to Radarr/Sonarr
6. [FlareSolverr](docs/flaresolverr.md) - install the Cloudflare bypass proxy (optional)
7. [ClamAV](docs/clamav.md) - install malware scanning (optional)
8. [Flatten Downloads](docs/flatten-downloads.md) - install the auto-flatten service (optional)
9. [Notifications](docs/notifications.md) - set up Discord webhook alerts (optional)

## Permissions

All services share a `mediadl` group for download directory access and a `plex` group for media library access. See [permissions](docs/permissions.md) for full details on the group model, setgid configuration, and how to verify everything is working.

## Service Management

```bash
# Check status of all services
sudo systemctl status radarr sonarr prowlarr rdt-client flaresolverr flatten-downloads clamav-daemon

# Restart a single service
sudo systemctl restart radarr

# View logs
sudo journalctl -u rdt-client -n 50
```

## Known Issues

- rdt-client's `appsettings.json` must be configured before first start (default Docker paths do not exist). See [rdt-client docs](docs/rdt-client.md#application-settings).
- FlareSolverr effectiveness depends on Chromium version. Debian 12 stable ships Chromium ~131 which may not solve newer Cloudflare challenges. See [FlareSolverr docs](docs/flaresolverr.md#chromium-compatibility).
- The flatten-downloads service only handles single-file mkv and mp4 folders. See [limitations](docs/flatten-downloads.md#limitations).
- Sonarr can show `No indexers available` if Prowlarr sync fails. See [Prowlarr troubleshooting](docs/prowlarr.md#troubleshooting).
