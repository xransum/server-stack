# Prowlarr

Indexer management and proxy. Prowlarr aggregates torrent indexers and automatically syncs them to Radarr and Sonarr, so you only need to configure indexers in one place.

- **Port**: 9696
- **Runs as**: `prowlarr` system user
- **Install path**: `/opt/Prowlarr`
- **Config/database**: `/var/lib/prowlarr`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [Radarr prerequisites](radarr.md#system-dependencies) for install steps)

## Install

Create the service user:

```bash
sudo useradd -r -s /usr/sbin/nologin prowlarr
```

Download and extract Prowlarr (uses the latest release):

```bash
PROWLARR_URL="https://github.com/Prowlarr/Prowlarr/releases/latest/download/Prowlarr.master.linux-core-x64.tar.gz"

sudo rm -rf /opt/Prowlarr
sudo mkdir -p /opt/Prowlarr
curl -fsSL "$PROWLARR_URL" -o /tmp/prowlarr.tar.gz
sudo tar -xzf /tmp/prowlarr.tar.gz -C /opt/Prowlarr --strip-components=1
sudo chown -R prowlarr:prowlarr /opt/Prowlarr
rm /tmp/prowlarr.tar.gz
```

Create the config directory:

```bash
sudo mkdir -p /var/lib/prowlarr
sudo chown prowlarr:prowlarr /var/lib/prowlarr
```

## Systemd Service

Create `/etc/systemd/system/prowlarr.service`:

```ini
[Unit]
Description=Prowlarr
After=network.target

[Service]
User=prowlarr
Group=mediadl
UMask=0002
ExecStart=/opt/Prowlarr/Prowlarr -nobrowser -data=/var/lib/prowlarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now prowlarr
```

## Firewall

```bash
sudo ufw allow from 192.168.1.0/24 to any port 9696
```

## Configuration

Open `http://localhost:9696` in your browser.

### Indexers

The following public indexers work well and do not require accounts:

| Indexer | Type | Notes |
|---|---|---|
| YTS | Movies | Best for movies. Near 100% Real-Debrid cache hit rate on popular releases. |
| TorrentsCSV | General | Categorized as "Other" in Prowlarr. Make sure it is enabled after syncing, not just synced. |
| Torrent Downloads | General | Supports TV season and episode search types. Good general fallback. |

Most other popular public indexers are protected by Cloudflare:

- **TorrentGalaxy**, **1337x**, **EZTV**, **LimeTorrents** - all require FlareSolverr to bypass Cloudflare, with limited and inconsistent success as of Chromium 131-136 on Debian 12

For reliable TV coverage, a private tracker is strongly recommended:

- **TorrentLeech**, **IPTorrents** - general purpose, no Cloudflare
- **BTN** - best for TV
- **PTP** - best for movies

Check r/trackers for open signups.

### Connect To Radarr

- Settings -> Apps -> Add Application -> Radarr
  - Prowlarr URL: `http://localhost:9696`
  - Radarr URL: `http://localhost:7878`
  - API key: found in Radarr -> Settings -> General
  - Sync Profile: Standard
  - Test and Save

### Connect To Sonarr

- Settings -> Apps -> Add Application -> Sonarr
  - Prowlarr URL: `http://localhost:9696`
  - Sonarr URL: `http://localhost:8989`
  - API key: found in Sonarr -> Settings -> General
  - Sync Profile: Standard
  - Test and Save

Prowlarr will automatically sync all configured indexers to both apps.

### FlareSolverr Integration

If you have [FlareSolverr](flaresolverr.md) running:

- Settings -> Indexer Proxies -> Add -> FlareSolverr
  - Host: `http://localhost:8191`
  - Test and Save
- When adding a Cloudflare-protected indexer, assign the FlareSolverr tag in the Tags field

## Service Management

```bash
sudo systemctl status prowlarr
sudo systemctl restart prowlarr
sudo journalctl -u prowlarr -n 50
```

## Updating

Prowlarr uses the latest release URL, so updating is the same as a fresh install:

```bash
sudo systemctl stop prowlarr
curl -fsSL "https://github.com/Prowlarr/Prowlarr/releases/latest/download/Prowlarr.master.linux-core-x64.tar.gz" -o /tmp/prowlarr.tar.gz
sudo rm -rf /opt/Prowlarr
sudo mkdir -p /opt/Prowlarr
sudo tar -xzf /tmp/prowlarr.tar.gz -C /opt/Prowlarr --strip-components=1
sudo chown -R prowlarr:prowlarr /opt/Prowlarr
rm /tmp/prowlarr.tar.gz
sudo systemctl start prowlarr
```

Your configuration in `/var/lib/prowlarr` is preserved across updates.

## Troubleshooting

### Indexers Not Syncing To Radarr/Sonarr

If Radarr or Sonarr show `No indexers available` after configuring Prowlarr:

1. Open Prowlarr -> Settings -> Apps
2. Open the Radarr or Sonarr entry
3. Re-save it without changes to force a re-sync
4. Check that the API key and URL are correct
