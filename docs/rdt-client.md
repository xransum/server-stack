# rdt-client

Real-Debrid download client. rdt-client acts as a qBittorrent-compatible API that Radarr and Sonarr can use as a download client. It sends torrents to Real-Debrid for debridding and then downloads the resulting files locally.

- **Port**: 6500
- **Runs as**: `rdtclient` system user
- **Install path**: `/opt/rdt-client`
- **Downloads**: `/mnt/raid/media/Downloads`
- **Database**: `/mnt/raid/media/Downloads/rdtclient.db`
- **Logs**: `/mnt/raid/media/Downloads/rdtclient.log`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [Radarr prerequisites](radarr.md#system-dependencies) for install steps)
- An active Real-Debrid premium subscription
- An API key from `https://real-debrid.com/apitoken`
- The `mediadl` group must exist. See [permissions](permissions.md).

## Install

Create the service user:

```bash
sudo useradd -r -s /usr/sbin/nologin rdtclient
sudo usermod -aG mediadl rdtclient
sudo usermod -aG plex rdtclient
```

Download and extract rdt-client:

```bash
RDTCLIENT_URL="https://github.com/rogerfar/rdt-client/releases/latest/download/RealDebridClient.zip"

sudo rm -rf /opt/rdt-client
sudo mkdir -p /opt/rdt-client
curl -fsSL "$RDTCLIENT_URL" -o /tmp/rdt-client.zip
sudo unzip -q /tmp/rdt-client.zip -d /opt/rdt-client
sudo chown -R rdtclient:rdtclient /opt/rdt-client
rm /tmp/rdt-client.zip
```

## Downloads Directory

Create the download directories and set permissions:

```bash
sudo mkdir -p /mnt/raid/media/Downloads/radarr /mnt/raid/media/Downloads/sonarr
sudo chown -R rdtclient:mediadl /mnt/raid/media/Downloads
sudo chmod -R g+rw /mnt/raid/media/Downloads
sudo chmod 2775 /mnt/raid/media/Downloads /mnt/raid/media/Downloads/radarr /mnt/raid/media/Downloads/sonarr
```

The setgid (`2775`) ensures new files inherit the `mediadl` group so Radarr and Sonarr can read and move them.

## Application Settings

The default `appsettings.json` assumes Docker paths that do not exist on a bare-metal install. Replace it with paths that match your setup.

Create `/opt/rdt-client/appsettings.json`:

```json
{
  "Logging": {
    "File": {
      "Path": "/mnt/raid/media/Downloads/rdtclient.log",
      "FileSizeLimitBytes": 5242880,
      "MaxRollingFiles": 5
    }
  },
  "Database": {
    "Path": "/mnt/raid/media/Downloads/rdtclient.db"
  },
  "Port": "6500",
  "BasePath": null
}
```

```bash
sudo chown rdtclient:rdtclient /opt/rdt-client/appsettings.json
```

This must be done before starting the service. If the default `/data/db` path is used, rdt-client will fail to start.

## Systemd Service

Create `/etc/systemd/system/rdt-client.service`:

```ini
[Unit]
Description=rdt-client
After=network.target

[Service]
User=rdtclient
Group=rdtclient
WorkingDirectory=/opt/rdt-client
ExecStart=/usr/bin/dotnet /opt/rdt-client/RdtClient.Web.dll
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rdt-client
```

## Firewall

```bash
sudo ufw allow from 192.168.1.0/24 to any port 6500
```

## Configuration

Open `http://localhost:6500` in your browser.

### First Visit

Create a local login account on the first visit.

### Provider

- Settings -> Provider -> set provider to RealDebrid
- Paste your API key from `https://real-debrid.com/apitoken`

> Note: the API Hostname field should be left blank or set exactly to `https://api.real-debrid.com/rest/1.0/`. Any other value causes URI parse errors.

### Download Client

- Settings -> Download Client -> set download path to `/mnt/raid/media/Downloads`
- Settings -> Download Client -> set mapped path to `/mnt/raid/media/Downloads` (same as download path since this is not a Docker setup)
- Settings -> Download Client -> set downloader to `Bezzad` (not Symlink, which requires an rclone mount)

### Exclude Junk Files

In Settings -> qBittorrent / *darr, add this regex to the excluded files field to prevent junk files from being downloaded alongside the media:

```text
(?i).*\.(txt|jpg|jpeg|png|torrent|nfo|exe|sh|bash|md[0-9])$
```

## Service Management

```bash
sudo systemctl status rdt-client
sudo systemctl restart rdt-client
sudo journalctl -u rdt-client -n 50
```

## Updating

```bash
sudo systemctl stop rdt-client
curl -fsSL "https://github.com/rogerfar/rdt-client/releases/latest/download/RealDebridClient.zip" -o /tmp/rdt-client.zip
sudo rm -rf /opt/rdt-client
sudo mkdir -p /opt/rdt-client
sudo unzip -q /tmp/rdt-client.zip -d /opt/rdt-client
sudo chown -R rdtclient:rdtclient /opt/rdt-client
rm /tmp/rdt-client.zip
```

After extracting, re-create `appsettings.json` (see [Application Settings](#application-settings) above) since it gets overwritten by the zip, then start the service:

```bash
sudo systemctl start rdt-client
```

The database at `/mnt/raid/media/Downloads/rdtclient.db` is preserved since it lives outside the install directory.

## Troubleshooting

### 429 Rate Limit Errors

If rdt-client shows `429` rate limit errors on first start, stop the service and wait 5-10 minutes for Real-Debrid's rate limiter to reset:

```bash
sudo systemctl stop rdt-client
# wait 5-10 minutes
sudo systemctl start rdt-client
```

This can happen if the API key was entered incorrectly multiple times.

### Downloads Creating Nested Folders

rdt-client sometimes creates a folder named after the file and puts the actual file inside it (e.g., `Movie.Name.mkv/Movie.Name.mkv`). This breaks Radarr/Sonarr import. See [flatten-downloads](flatten-downloads.md) for the automated solution.
