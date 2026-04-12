# Radarr

Movie management and automation. Radarr monitors for new movies, searches indexers via Prowlarr, sends torrents to rdt-client, and imports completed downloads into your media library.

- **Port**: 7878
- **Runs as**: `radarr` system user
- **Install path**: `/opt/Radarr`
- **Config/database**: `/var/lib/radarr`
- **Media root**: `/mnt/raid/media/Videos/Movies`

## Prerequisites

- ASP.NET Core runtime 10.0 (see [System Dependencies](#system-dependencies))
- The `plex` group must exist (Radarr is added to it for media library access)
- The `mediadl` group must exist (for shared download directory access). See [permissions](permissions.md).

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

Create the service user:

```bash
sudo useradd -r -s /usr/sbin/nologin radarr
sudo usermod -aG plex radarr
sudo usermod -aG mediadl radarr
```

Download and extract Radarr (replace the version as needed):

```bash
RADARR_VERSION="5.21.1.9799"
RADARR_URL="https://github.com/Radarr/Radarr/releases/download/v${RADARR_VERSION}/Radarr.master.${RADARR_VERSION}.linux-core-x64.tar.gz"

sudo rm -rf /opt/Radarr
sudo mkdir -p /opt/Radarr
curl -fsSL "$RADARR_URL" -o /tmp/radarr.tar.gz
sudo tar -xzf /tmp/radarr.tar.gz -C /opt/Radarr --strip-components=1
sudo chown -R radarr:radarr /opt/Radarr
rm /tmp/radarr.tar.gz
```

Create the config directory:

```bash
sudo mkdir -p /var/lib/radarr
sudo chown radarr:radarr /var/lib/radarr
```

## Systemd Service

Create `/etc/systemd/system/radarr.service`:

```ini
[Unit]
Description=Radarr
After=network.target

[Service]
User=radarr
Group=radarr
ExecStart=/opt/Radarr/Radarr -nobrowser -data=/var/lib/radarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now radarr
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

### Quality Profile

- Settings -> Profiles -> edit your quality profile
- Disable CAM, Telecine, and Telesync to avoid bad quality grabs

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
sudo chown -R radarr:radarr /opt/Radarr
rm /tmp/radarr.tar.gz
sudo systemctl start radarr
```

Your configuration in `/var/lib/radarr` is preserved across updates.
