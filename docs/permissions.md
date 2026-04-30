# Permissions

All services run as a single shared `media` user. This eliminates cross-service
permission issues that arise from running each service as its own user.

Plex runs as its own user but is added to the `media` group so it can read the library.

## Create the media user

```bash
sudo useradd -r -s /bin/false -d /mnt/raid/media media
```

## Add your personal user and Plex to the media group

```bash
sudo usermod -aG media $USER
sudo usermod -aG media plex
```

Log out and back in for the group change to take effect on your session.

## Create the directory structure

```bash
sudo mkdir -p /mnt/raid/media/Videos/Movies
sudo mkdir -p "/mnt/raid/media/Videos/TV Shows"
sudo mkdir -p /mnt/raid/media/Downloads/radarr
sudo mkdir -p /mnt/raid/media/Downloads/sonarr
```

## Set ownership

```bash
sudo chown -R media:media /mnt/raid/media/Downloads
sudo chown -R media:media /mnt/raid/media/Videos
sudo chown -R media:media /var/lib/sonarr
sudo chown -R media:media /var/lib/radarr
sudo chown -R media:media /var/lib/prowlarr
sudo chown -R media:media /opt/rdt-client
sudo chown -R media:media /opt/Sonarr
sudo chown -R media:media /opt/Radarr
sudo chown -R media:media /opt/Prowlarr
```

## Verify

All service processes should show `media` as the owner:

```bash
ps aux | grep -E "Sonarr|Radarr|dotnet"
```

Download and library directories should be owned by `media`:

```bash
ls -la /mnt/raid/media/Downloads/
ls -la /mnt/raid/media/Videos/
```
