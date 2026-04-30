# Sonarr

TV show management and automation. Sonarr monitors for new episodes, searches indexers via Prowlarr, sends torrents to rdt-client, and imports completed downloads into your media library.

- **Port**: 8989
- **Runs as**: `media` system user
- **Install path**: `/opt/Sonarr`
- **Config/database**: `/var/lib/sonarr`
- **Media root**: `/mnt/raid/media/Videos/TV Shows`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [Radarr prerequisites](radarr.md#system-dependencies) for install steps)
- The `media` user must exist and own the install paths. See [permissions](permissions.md).

## Install

Create the config directory and set ownership:

```bash
sudo mkdir -p /var/lib/sonarr
sudo chown media:media /var/lib/sonarr
```

Download and extract Sonarr (replace the version as needed):

```bash
SONARR_VERSION="4.0.17.2953"
SONARR_URL="https://github.com/Sonarr/Sonarr/releases/download/v${SONARR_VERSION}/Sonarr.develop.${SONARR_VERSION}.linux-x64.tar.gz"

sudo rm -rf /opt/Sonarr
sudo mkdir -p /opt/Sonarr
curl -fsSL "$SONARR_URL" -o /tmp/sonarr.tar.gz
sudo tar -xzf /tmp/sonarr.tar.gz -C /opt/Sonarr --strip-components=1
sudo chown -R media:media /opt/Sonarr
rm /tmp/sonarr.tar.gz
```

## Firewall

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8989
```

## Configuration

Open `http://localhost:8989` in your browser.

### Download Client

- Settings -> Download Clients -> Add -> qBittorrent
  - Host: `localhost`
  - Port: `6500`
  - Username/Password: your rdt-client credentials
  - Category: `sonarr`
  - Test and Save

### Root Folder

- Settings -> Media Management -> Root Folders -> Add -> `/mnt/raid/media/Videos/TV Shows`

### ClamAV Integration

See [clamav.md](clamav.md) for setting up automatic malware scanning on import.

- Settings -> Connect -> Add -> Custom Script
  - Name: ClamAV Scan
  - Path: `/usr/local/bin/scan-media.sh`
  - Trigger: On Import

## Service Management

```bash
sudo systemctl status sonarr
sudo systemctl restart sonarr
sudo journalctl -u sonarr -n 50
```

## Updating

Stop the service, replace the binaries, and restart:

```bash
SONARR_VERSION="x.x.x.xxxx"  # new version
sudo systemctl stop sonarr
curl -fsSL "https://github.com/Sonarr/Sonarr/releases/download/v${SONARR_VERSION}/Sonarr.develop.${SONARR_VERSION}.linux-x64.tar.gz" -o /tmp/sonarr.tar.gz
sudo rm -rf /opt/Sonarr
sudo mkdir -p /opt/Sonarr
sudo tar -xzf /tmp/sonarr.tar.gz -C /opt/Sonarr --strip-components=1
sudo chown -R media:media /opt/Sonarr
rm /tmp/sonarr.tar.gz
sudo systemctl start sonarr
```

Your configuration in `/var/lib/sonarr` is preserved across updates.

## Troubleshooting

### Not Importing After Downloads Complete

If files are in the downloads folder but Sonarr is not importing them:

1. Check that the `media` user owns the downloads directory:

```bash
ls -la /mnt/raid/media/Downloads/sonarr/
```

2. Use Manual Import as a fallback:

- Sonarr -> Wanted -> Manual Import
- Point it at `/mnt/raid/media/Downloads/sonarr`
- Match the files to episodes and import them

### No Indexers Available

If Sonarr shows `No indexers available` health warnings, Prowlarr sync may not have pushed indexers correctly. In Prowlarr, go to Settings -> Apps, open the Sonarr entry, and re-save it to force a re-sync.

### Mapped A Show To The Wrong Folder

If Sonarr mapped a show to an existing folder from your pre-existing media library, remove the show from Sonarr without deleting files (do not check the delete files box), then re-add it with the correct path.

## Service file

Copy the service file from the repo and enable it:

```bash
sudo cp services/sonarr.service /etc/systemd/system/sonarr.service
sudo systemctl daemon-reload
sudo systemctl enable --now sonarr
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
profile a show uses.

## Notifications

See [notifications](notifications.md) for Discord webhook setup.
