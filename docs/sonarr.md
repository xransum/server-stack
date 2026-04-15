# Sonarr

TV show management and automation. Sonarr monitors for new episodes, searches indexers via Prowlarr, sends torrents to rdt-client, and imports completed downloads into your media library.

- **Port**: 8989
- **Runs as**: `sonarr` system user
- **Install path**: `/opt/Sonarr`
- **Config/database**: `/var/lib/sonarr`
- **Media root**: `/mnt/raid/media/Videos/TV Shows`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [Radarr prerequisites](radarr.md#system-dependencies) for install steps)
- The `plex` group must exist (Sonarr is added to it for media library access)
- The `mediadl` group must exist (for shared download directory access). See [permissions](permissions.md).

## Install

Create the service user:

```bash
sudo useradd -r -s /usr/sbin/nologin sonarr
sudo usermod -aG plex sonarr
sudo usermod -aG mediadl sonarr
```

Download and extract Sonarr (replace the version as needed):

```bash
SONARR_VERSION="4.0.17.2953"
SONARR_URL="https://github.com/Sonarr/Sonarr/releases/download/v${SONARR_VERSION}/Sonarr.develop.${SONARR_VERSION}.linux-x64.tar.gz"

sudo rm -rf /opt/Sonarr
sudo mkdir -p /opt/Sonarr
curl -fsSL "$SONARR_URL" -o /tmp/sonarr.tar.gz
sudo tar -xzf /tmp/sonarr.tar.gz -C /opt/Sonarr --strip-components=1
sudo chown -R sonarr:sonarr /opt/Sonarr
rm /tmp/sonarr.tar.gz
```

Create the config directory:

```bash
sudo mkdir -p /var/lib/sonarr
sudo chown sonarr:sonarr /var/lib/sonarr
```

## Systemd Service

Create `/etc/systemd/system/sonarr.service`:

```ini
[Unit]
Description=Sonarr
After=network.target

[Service]
User=sonarr
Group=mediadl
UMask=0002
ExecStart=/opt/Sonarr/Sonarr -nobrowser -data=/var/lib/sonarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sonarr
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

### Quality Profile

- Set **Language** to `English` in each quality profile. This filters at the profile level so a separate custom format for English-only is redundant.
- Set **Maximum Size** at the indexer level (Settings -> Indexers -> edit indexer -> Maximum Size) rather than per-profile. This applies a global cap across all profiles.

### Custom Formats

#### Bad Sources

Create a custom format to block low-quality sources:

- Settings -> Custom Formats -> Add
- Name: `Bad Sources`
- Add conditions (type: Source): `UNKNOWN`, `CAM`, `TELESYNC`, `TELECINE`, `WORKPRINT`
- Save

In each quality profile, set the score for Bad Sources to `-10000`.

#### Blocked Releases

Create a custom format to block unwanted release groups and foreign-language uploads:

- Settings -> Custom Formats -> Add
- Name: `Blocked Releases`
- Add conditions (type: Release Title, use regex):
  - Cyrillic characters: `[А-Яа-яЁё]`
  - Known bad groups: `\b(Zamez|Hamster|HDCLUB)\b`
  - Multi-language indicators (adjust as needed): `\b(MULTI|MULTi)\b`
- Save

In each quality profile, set the score for Blocked Releases to `-10000`.

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
sudo chown -R sonarr:sonarr /opt/Sonarr
rm /tmp/sonarr.tar.gz
sudo systemctl start sonarr
```

Your configuration in `/var/lib/sonarr` is preserved across updates.

## Troubleshooting

### Not Importing After Downloads Complete

If files are in the downloads folder but Sonarr is not importing them:

1. Check permissions. Sonarr needs read and move access to the download folder. Verify it is in the `mediadl` group:

```bash
groups sonarr
```

If `mediadl` is missing:

```bash
sudo usermod -aG mediadl sonarr
sudo systemctl restart sonarr
```

2. Use Manual Import as a fallback:

- Sonarr -> Wanted -> Manual Import
- Point it at `/mnt/raid/media/Downloads/sonarr`
- Match the files to episodes and import them

### No Indexers Available

If Sonarr shows `No indexers available` health warnings, Prowlarr sync may not have pushed indexers correctly. In Prowlarr, go to Settings -> Apps, open the Sonarr entry, and re-save it to force a re-sync.

### Mapped A Show To The Wrong Folder

If Sonarr mapped a show to an existing folder from your pre-existing media library, remove the show from Sonarr without deleting files (do not check the delete files box), then re-add it with the correct path.
