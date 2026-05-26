# Radarr

Movie management and automation. Radarr monitors for new movies, searches indexers via Prowlarr, sends torrents to rdt-client, and imports completed downloads into your media library.

- **Port**: 7878
- **Runs as**: `media` system user
- **Install path**: `/opt/Radarr`
- **Config/database**: `/var/lib/radarr`
- **Media root**: `/mnt/raid/media/Videos/Movies`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [System Dependencies](#system-dependencies))
- The `media` user must exist and own the install paths. See [permissions](permissions.md).

## System Dependencies

```bash
sudo apt update
sudo apt install -y curl wget mediainfo sqlite3
```

If ASP.NET Core 10.0 is not already installed:

```bash
wget -q https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O /tmp/ms-prod.deb
sudo dpkg -i /tmp/ms-prod.deb
sudo apt update
sudo apt install -y aspnetcore-runtime-10.0
rm /tmp/ms-prod.deb
```

## Install

Create the config directory and set ownership:

```bash
sudo mkdir -p /var/lib/radarr
sudo chown media:media /var/lib/radarr
```

Download and extract Radarr (replace the version as needed):

```bash
RADARR_VERSION="5.21.1.9799"
RADARR_URL="https://github.com/Radarr/Radarr/releases/download/v${RADARR_VERSION}/Radarr.master.${RADARR_VERSION}.linux-core-x64.tar.gz"

sudo rm -rf /opt/Radarr
sudo mkdir -p /opt/Radarr
curl -fsSL "$RADARR_URL" -o /tmp/radarr.tar.gz
sudo tar -xzf /tmp/radarr.tar.gz -C /opt/Radarr --strip-components=1
sudo chown -R media:media /opt/Radarr
rm /tmp/radarr.tar.gz
```

## Firewall

Allow access from your LAN only:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 7878
```

## Configuration

Open `http://localhost:7878` in your browser.

### Download Client

- Settings -> Download Clients -> Add -> qBittorrent
  - Host: `localhost`
  - Port: `6500`
  - Username/Password: your rdt-client credentials
  - Category: `radarr`
  - Test and Save

### Root Folder

- Settings -> Media Management -> Root Folders -> Add -> `/mnt/raid/media/Videos/Movies`

### ClamAV Integration

See [clamav.md](clamav.md) for setting up automatic malware scanning on import.

- Settings -> Connect -> Add -> Custom Script
  - Name: ClamAV Scan
  - Path: `/usr/local/bin/scan-media.sh`
  - Trigger: On Import

## Service Management

```bash
sudo systemctl status radarr
sudo systemctl restart radarr
sudo journalctl -u radarr -n 50
```

## Updating

Stop the service, replace the binaries, and restart:

```bash
RADARR_VERSION="x.x.x.xxxx"  # new version
sudo systemctl stop radarr
curl -fsSL "https://github.com/Radarr/Radarr/releases/download/v${RADARR_VERSION}/Radarr.master.${RADARR_VERSION}.linux-core-x64.tar.gz" -o /tmp/radarr.tar.gz
sudo rm -rf /opt/Radarr
sudo mkdir -p /opt/Radarr
sudo tar -xzf /tmp/radarr.tar.gz -C /opt/Radarr --strip-components=1
sudo chown -R media:media /opt/Radarr
rm /tmp/radarr.tar.gz
sudo systemctl start radarr
```

Your configuration in `/var/lib/radarr` is preserved across updates.

## Service file

Copy the service file from the repo and enable it:

```bash
sudo cp services/radarr.service /etc/systemd/system/radarr.service
sudo systemctl daemon-reload
sudo systemctl enable --now radarr
```

## Quality profiles

### Language
Set Language to `English` in each quality profile (Settings > Profiles) to filter
non-English releases.

### Bad Sources custom format
Go to Settings > Custom Formats > + and create a format called `Bad Sources`.
Add a Source condition for each of the following, then set the score to `-10000`
in all quality profiles:
- UNKNOWN
- CAM
- TELESYNC
- TELECINE
- WORKPRINT

### Blocked Releases custom format
Create a custom format called `Blocked Releases` with a Release Title condition
using the regex `[А-Яа-яЁё]` to catch Cyrillic characters in release names.
Set the score to `-10000` in all quality profiles.

### Size limits
Set Maximum Size at the indexer level (Settings > Indexers > edit each indexer)
rather than per quality profile. This applies a global cap regardless of which
profile a movie uses.

### Remux files
Remux files are uncompressed disc rips and can be extremely large (50-70GB for a
single movie). A standard 4K WEB-DL encode is typically 15-25GB with no perceptible
quality difference for home viewing. Consider unchecking Remux quality tiers from
your profile unless you specifically want them.

## Replacing oversized files

To replace an existing file with a smaller encode without going through Overseerr:

1. In the quality profile uncheck the quality tier the current file matches
   (e.g. uncheck Remux-2160p if the file is a remux)
2. Go to the movie page in Radarr and click Automatic Search
3. Radarr will grab a replacement and swap the file automatically
4. Re-enable the quality tier in the profile after the replacement downloads

Do not delete the movie from Radarr or Overseerr - just update the profile and search.

## Notifications

See [notifications](notifications.md) for Discord webhook setup.

---

## Docker install

In the Docker Compose lab target, Radarr runs as a container in the
`indexers` profile using the official LinuxServer image. See
`compose/docker-compose.yml` for the full service definition and
`compose/README.md` for startup instructions.

Same hardlink requirement as Sonarr: `/mnt/media` must be mounted at the
same path inside the container. The compose file bind-mounts
`/mnt/media:/mnt/media`. Do not split `downloads/` and `movies/` across
separate mounts — hardlinks require the same filesystem.

Radarr's SQLite database lives in a named Docker volume (`radarr-config`)
on the VM's local disk, not on NFS.
