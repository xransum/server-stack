# media-stack

Automated install script for a self-hosted media automation stack on Debian Linux using Real-Debrid.

## Stack

| Service | Purpose | Port |
|---|---|---|
| Radarr | Movie management | 7878 |
| Sonarr | TV show management | 8989 |
| Prowlarr | Indexer management | 9696 |
| rdt-client | Real-Debrid download client | 6500 |
| FlareSolverr | Cloudflare bypass proxy for indexers | 8191 |
| Plex | Media server | 32400 |
| Overseerr | Request UI | 5055 |
| Tautulli | Plex stats | 8181 |

Plex, Overseerr, and Tautulli are assumed to already be installed and are not handled by this script.

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
- `pyenv` and `pyenv-virtualenv` installed for the user running `sudo`
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
    Radarr/
    Sonarr/
    Prowlarr/
    rdt-client/

/var/lib/
    radarr/             <- Radarr config/database
    sonarr/             <- Sonarr config/database
    prowlarr/           <- Prowlarr config/database

~/.pyenv/versions/
    flaresolverr-env/   <- FlareSolverr Python 3.11 virtualenv
```

## Install

Edit the config variables at the top of `install.sh` to match your paths and media group, then:

```bash
chmod +x install.sh
sudo ./install.sh
```

The script installs and enables all services as systemd units. They will start automatically on boot.

## Post-Install Configuration

### 1. rdt-client (`http://localhost:6500`)

- On first visit create a local login account
- Settings -> Provider -> set provider to RealDebrid and paste your API key from `https://real-debrid.com/apitoken`
- Settings -> Download Client -> set download path to `/mnt/raid/media/Downloads`
- Settings -> Download Client -> set mapped path to `/mnt/raid/media/Downloads` (same as download path since this is not a Docker setup)
- Settings -> Download Client -> set downloader to `Bezzad`

> Note: if rdt-client shows `429` rate limit errors on first start, stop the service for 5-10 minutes to let Real-Debrid's rate limiter reset, then start it again. This can happen if the API key was entered incorrectly multiple times.

> Note: the API Hostname field should be left blank or set exactly to `https://api.real-debrid.com/rest/1.0/`. Any other value causes URI parse errors.

### 2. Prowlarr (`http://localhost:9696`)

Add indexers. The following are recommended public indexers that do not require accounts:

- **YTS** - best for movies, extremely high RD cache hit rate
- **TorrentsCSV** - general purpose
- **Torrent Downloads** - general purpose, supports TV season and episode search

Most other popular public indexers like TorrentGalaxy, 1337x, EZTV, and LimeTorrents are protected by Cloudflare. FlareSolverr can sometimes bypass these, but success varies by site and Chromium version.

For reliable TV coverage, a private tracker like TorrentLeech or IPTorrents is strongly recommended.

Then connect Prowlarr to Radarr and Sonarr:

- Settings -> Apps -> Add Application -> Radarr
  - Prowlarr URL: `http://localhost:9696`
  - Radarr URL: `http://localhost:7878`
  - API key: found in Radarr -> Settings -> General
  - Sync Profile: Standard
- Repeat for Sonarr at `http://localhost:8989`

Prowlarr will automatically sync indexers to both apps.

### 3. Radarr (`http://localhost:7878`)

- Settings -> Download Clients -> Add -> qBittorrent
  - Host: `localhost`
  - Port: `6500`
  - Username/Password: your rdt-client credentials
  - Category: `radarr`
  - Test and Save
- Settings -> Media Management -> Root Folders -> `/mnt/raid/media/Videos/Movies`
- Settings -> Profiles -> edit your quality profile and disable CAM, Telecine, and Telesync to avoid bad quality grabs

### 4. Sonarr (`http://localhost:8989`)

Same download client setup as Radarr:

- Settings -> Download Clients -> Add -> qBittorrent
  - Host: `localhost`
  - Port: `6500`
  - Username/Password: your rdt-client credentials
  - Category: `sonarr`
  - Test and Save
- Settings -> Media Management -> Root Folders -> `/mnt/raid/media/Videos/TV Shows`

### 5. Overseerr (`http://localhost:5055`)

- Settings -> Services -> Add Radarr
  - URL: `http://localhost:7878`
  - API key: from Radarr -> Settings -> General
- Repeat for Sonarr at `http://localhost:8989`

### 6. FlareSolverr (`http://localhost:8191`)

FlareSolverr lets Prowlarr bypass Cloudflare protection on indexers. This installer runs it from a `pyenv` Python 3.11 virtualenv because:

- The prebuilt binary currently expects a newer glibc than Debian 12 ships
- The pip package is not compatible with Python 3.13 because the `cgi` module was removed

To add it in Prowlarr:

- Settings -> Indexer Proxies -> Add -> FlareSolverr
  - Host: `http://localhost:8191`
  - Test and Save
- When adding a Cloudflare-protected indexer, assign the FlareSolverr tag in the Tags field

## Service Management

```bash
# Check status
sudo systemctl status radarr
sudo systemctl status sonarr
sudo systemctl status prowlarr
sudo systemctl status rdt-client
sudo systemctl status flaresolverr

# Restart a service
sudo systemctl restart radarr

# View logs
sudo journalctl -u rdt-client -n 50
sudo journalctl -u radarr -n 50
sudo journalctl -u flaresolverr -n 50
```

## Permissions

Each service runs as its own service user except FlareSolverr:

| Service | User | Group |
|---|---|---|
| Radarr | radarr | radarr, plex, rdtclient |
| Sonarr | sonarr | sonarr, plex, rdtclient |
| Prowlarr | prowlarr | prowlarr |
| rdt-client | rdtclient | rdtclient, plex |
| FlareSolverr | your sudo user | your user's primary group |

The `radarr` and `sonarr` users are added to both the configured media group and the `rdtclient` group so they can import completed downloads. The `rdtclient` user is also added to the configured media group so downloaded files stay accessible to Plex after import.

The media directories use setgid (`chmod g+s`) so new files inherit the shared media group automatically. The downloads directory and its category folders are also setgid so any new subdirectories created by `rdt-client` inherit the `rdtclient` group.

FlareSolverr runs as your actual user because it needs access to your `pyenv` installation and Python virtualenv.

If you already installed the stack before this permissions fix, apply the same group changes manually and restart the affected services:

```bash
sudo usermod -aG rdtclient radarr
sudo usermod -aG rdtclient sonarr
sudo usermod -aG plex rdtclient
sudo chmod g+s /mnt/raid/media/Downloads
sudo systemctl restart radarr sonarr rdt-client
```

## Ports

All *arr and rdt-client ports are firewalled to LAN only (`192.168.1.0/24`) by default. Plex (`32400`), Overseerr (`5055`), and Tautulli (`8181`) are left as-is since they may need remote access.

To open a port publicly:

```bash
sudo ufw allow 7878
```

## Indexer Notes

- **YTS** - movies only, near 100% RD cache hit rate on popular releases
- **TorrentsCSV** - general but categorized as Other in Prowlarr, so make sure it is enabled and not just synced
- **Torrent Downloads** - supports TV season and episode search types, good general fallback
- Most Cloudflare-protected public indexers remain unreliable even with FlareSolverr
- Private trackers are the most reliable long-term option and avoid Cloudflare entirely

## Known Issues

- rdt-client database and log paths must be set in `appsettings.json` before the service starts because the default `/data/db` path does not exist on a bare-metal install
- rdt-client runs as the `rdtclient` user, so the downloads directory must be owned by that user and shared with `radarr` and `sonarr` through the `rdtclient` group
- FlareSolverr requires `pyenv` and `pyenv-virtualenv` for the sudo user, plus the system Chromium and Xvfb packages the installer adds
- Sonarr can show `No indexers available` health warnings if Prowlarr sync does not push indexers correctly; re-saving the Sonarr app entry in Prowlarr forces a re-sync

## Malware Scanning

ClamAV is used to automatically scan downloaded media files after Sonarr and Radarr import them.

### Install ClamAV

The installer now installs `clamav` and `clamav-daemon` and creates `/usr/local/bin/scan-media.sh` for you.

If you want to install it manually instead:

```bash
sudo apt install -y clamav clamav-daemon
sudo systemctl stop clamav-freshclam
sudo freshclam
sudo systemctl start clamav-freshclam
sudo systemctl enable clamav-daemon
```

### Scan Script

The generated `/usr/local/bin/scan-media.sh` script:

```bash
#!/bin/bash

file="$1"

if [ -z "$file" ] || [ ! -e "$file" ]; then
    exit 0
fi

clamscan --no-summary --quiet "$file"
status=$?

if [ "$status" -eq 1 ]; then
    logger -t clamav "INFECTED FILE DETECTED: $file"
    rm -f "$file"
elif [ "$status" -gt 1 ]; then
    logger -t clamav "ClamAV scan failed for $file with exit code $status"
fi
```

### Connect To Radarr And Sonarr

In both Radarr and Sonarr:

- Settings -> Connect -> Add -> Custom Script
  - Name: ClamAV Scan
  - Path: `/usr/local/bin/scan-media.sh`
  - Trigger: On Import
  - Save

Every imported file will be scanned automatically after it lands in your media library.

### Exclude File Types From rdt-client Downloads

In rdt-client -> Settings -> qBittorrent / *darr, add this regex to the excluded files field to prevent junk files from being downloaded alongside the media:

```text
(?i).*\.(txt|jpg|jpeg|png|torrent|nfo|exe|sh|bash|md[0-9])$
```

## Quality And Language Filtering

### Block Foreign Dubs In Sonarr

To prevent Sonarr from grabbing foreign language dubs, add a Custom Format:

- Settings -> Custom Formats -> Add
- Add a condition: Language -> English, check Except Language
- Save the custom format
- Go to your quality profile and set this custom format score to `-10000`

This effectively blacklists any non-English release.

### Block CAM Releases

In Radarr and Sonarr quality profiles, disable CAM, Telecine, and Telesync qualities so they are never grabbed.

## Troubleshooting Import Issues

### Files Downloading As Folders

`rdt-client` sometimes creates a folder named after the `.mkv` file and puts the actual file inside it, which breaks Sonarr and Radarr import. Flatten them with:

```bash
cd /mnt/raid/media/Downloads/sonarr
for dir in *.mkv; do
    if [ -d "$dir" ]; then
        sudo mv "$dir/$dir" "${dir}.tmp"
        sudo rm -rf "$dir"
        sudo mv "${dir}.tmp" "$dir"
    fi
done

for dir in */; do
    if [ -d "$dir" ]; then
        sudo find "$dir" -name "*.mkv" -exec sudo mv {} . \;
        sudo rm -rf "$dir"
    fi
done
```

### Sonarr Not Importing After Downloads Complete

If files are in the downloads folder but Sonarr is not importing them:

1. Check permissions. Sonarr needs read and move access to the download folder.

```bash
sudo chown -R sonarr:sonarr /mnt/raid/media/Downloads/sonarr
```

2. Use Manual Import as a fallback:

- Sonarr -> Wanted -> Manual Import
- Point it at `/mnt/raid/media/Downloads/sonarr`
- Match the files to episodes and import them

### Sonarr Mapped A Show To The Wrong Folder

If Sonarr mapped a show to an existing folder from your pre-existing media library, remove the show from Sonarr without deleting files, then edit the show path to point at the correct folder.
