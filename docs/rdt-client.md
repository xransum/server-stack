# rdt-client

Real-Debrid download client. rdt-client acts as a qBittorrent-compatible API that Radarr and Sonarr can use as a download client. It sends torrents to Real-Debrid for debridding and then downloads the resulting files locally.

- **Port**: 6500
- **Runs as**: `media` system user
- **Install path**: `/opt/rdt-client`
- **Downloads**: `/mnt/raid0/media/Downloads`
- **Database**: `/mnt/raid0/media/Downloads/rdtclient.db`
- **Logs**: `/mnt/raid0/media/Downloads/rdtclient.log`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [Radarr prerequisites](radarr.md#system-dependencies) for install steps)
- An active Real-Debrid premium subscription
- An API key from `https://real-debrid.com/apitoken`
- The `media` user must exist. See [permissions](permissions.md).

## Install

Download and extract rdt-client:

```bash
RDTCLIENT_URL="https://github.com/rogerfar/rdt-client/releases/latest/download/RealDebridClient.zip"

sudo rm -rf /opt/rdt-client
sudo mkdir -p /opt/rdt-client
curl -fsSL "$RDTCLIENT_URL" -o /tmp/rdt-client.zip
sudo unzip -q /tmp/rdt-client.zip -d /opt/rdt-client
sudo chown -R media:media /opt/rdt-client
rm /tmp/rdt-client.zip
```

## Downloads Directory

Create the download directories and set permissions:

```bash
sudo mkdir -p /mnt/raid0/media/Downloads/radarr /mnt/raid0/media/Downloads/sonarr
sudo chown -R media:media /mnt/raid0/media/Downloads
```

## Application Settings

The default `appsettings.json` assumes Docker paths that do not exist on a bare-metal install. Replace it with paths that match your setup.

Create `/opt/rdt-client/appsettings.json`:

```json
{
  "Logging": {
    "File": {
      "Path": "/mnt/raid0/media/Downloads/rdtclient.log",
      "FileSizeLimitBytes": 5242880,
      "MaxRollingFiles": 5
    }
  },
  "Database": {
    "Path": "/mnt/raid0/media/Downloads/rdtclient.db"
  },
  "Port": "6500",
  "BasePath": null
}
```

```bash
sudo chown media:media /opt/rdt-client/appsettings.json
```

This must be done before starting the service. If the default `/data/db` path is used, rdt-client will fail to start.

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

- Settings -> Download Client -> set download path to `/mnt/raid0/media/Downloads`
- Settings -> Download Client -> set mapped path to `/mnt/raid0/media/Downloads` (same as download path since this is not a Docker setup)
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
sudo chown -R media:media /opt/rdt-client
rm /tmp/rdt-client.zip
```

After extracting, re-create `appsettings.json` (see [Application Settings](#application-settings) above) since it gets overwritten by the zip, then start the service:

```bash
sudo systemctl start rdt-client
```

The database at `/mnt/raid0/media/Downloads/rdtclient.db` is preserved since it lives outside the install directory.

## Troubleshooting

### 429 Rate Limit Errors

If rdt-client shows `429` rate limit errors on first start, stop the service and wait 5-10 minutes for Real-Debrid's rate limiter to reset:

```bash
sudo systemctl stop rdt-client
# wait 5-10 minutes
sudo systemctl start rdt-client
```

This can happen if the API key was entered incorrectly multiple times.

## Service file

Copy the service file from the repo and enable it:

```bash
sudo cp services/rdt-client.service /etc/systemd/system/rdt-client.service
sudo systemctl daemon-reload
sudo systemctl enable --now rdt-client
```

## Post-install configuration

After starting rdt-client and logging in at http://localhost:6500, configure the
following in Settings > qBittorrent / *darr:

- **Post Download Action**: Set to `Remove Torrent From Client And Provider` to
  automatically clean up completed downloads
- **Exclude files**: Add `.*\.(txt|jpg|jpeg|png|torrent|nfo|exe|sh|bash|md[0-9])$`
  to skip junk files included in some torrents

---

## Docker install

In the Docker Compose lab target, rdt-client runs as a container in the
`indexers` profile using the `rogerfar/rdtclient` image. See
`compose/docker-compose.yml` for the full service definition.

The `/mnt/media/downloads` directory is bind-mounted into the container
at the same path. The rdt-client database (`rdtclient.db`) lives in a
named Docker volume (`rdt-client-config`) on the VM's local disk.

Inside the Docker network, Sonarr and Radarr connect to rdt-client at
`http://rdt-client:6500` — use this URL when configuring the download
client in Sonarr/Radarr's Settings → Download Clients.
