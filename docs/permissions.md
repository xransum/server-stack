# Permissions

How file ownership and group permissions work across the media stack.

## Service Users

Each service runs as its own dedicated system user:

| Service | User | Created By |
|---|---|---|
| Radarr | `radarr` | installer or `useradd -r -s /usr/sbin/nologin radarr` |
| Sonarr | `sonarr` | installer or `useradd -r -s /usr/sbin/nologin sonarr` |
| Prowlarr | `prowlarr` | installer or `useradd -r -s /usr/sbin/nologin prowlarr` |
| rdt-client | `rdtclient` | installer or `useradd -r -s /usr/sbin/nologin rdtclient` |
| FlareSolverr | your user | runs under your account (needs pyenv access) |
| Plex | `plex` | Plex installer |
| Tautulli | `tautulli` | Tautulli installer |
| Overseerr | your user | runs under your account |

## Shared Media Group (mediadl)

The `mediadl` group provides shared read/write access to the downloads directory so all services can cooperate:

- **rdt-client** downloads files into the directory
- **Radarr** and **Sonarr** need to read and move files out of it
- **Plex** needs read access after files are imported
- **Your user** needs access to manage files without sudo

### Create The Group

```bash
sudo groupadd mediadl
```

### Add Users To The Group

```bash
sudo usermod -aG mediadl rdtclient
sudo usermod -aG mediadl radarr
sudo usermod -aG mediadl sonarr
sudo usermod -aG mediadl prowlarr
sudo usermod -aG mediadl plex
sudo usermod -aG mediadl tautulli
sudo usermod -aG mediadl kevin  # replace with your username
```

### Apply To Downloads Directory

```bash
sudo chown -R rdtclient:mediadl /mnt/raid/media/Downloads
sudo chmod -R g+rw /mnt/raid/media/Downloads
sudo chmod g+s /mnt/raid/media/Downloads/radarr
sudo chmod g+s /mnt/raid/media/Downloads/sonarr
```

The setgid bit (`g+s`) on the category subfolders means any new files rdt-client creates inside them will automatically inherit the `mediadl` group. Without setgid, new files would be owned by the `rdtclient` group and other services would not be able to access them.

### Restart Services After Group Changes

Group membership changes only take effect after the process restarts:

```bash
sudo systemctl restart radarr sonarr rdt-client prowlarr plex tautulli
```

## Media Library Group (plex)

The media library directories (`Movies`, `TV Shows`) need to be readable by Plex. The `plex` group (created by the Plex installer) is used for this.

### Add Services To The Plex Group

Radarr, Sonarr, and rdt-client are added to the `plex` group so files they write into the media library are readable by Plex:

```bash
sudo usermod -aG plex radarr
sudo usermod -aG plex sonarr
sudo usermod -aG plex rdtclient
```

### Apply Setgid To Media Directories

```bash
sudo chgrp -R plex /mnt/raid/media/Videos
sudo chmod 2775 /mnt/raid/media/Videos /mnt/raid/media/Videos/Movies "/mnt/raid/media/Videos/TV Shows"
```

The `2775` permission means:
- Owner: read/write/execute
- Group (`plex`): read/write/execute
- Others: read/execute
- Setgid: new files inherit the `plex` group

## Summary

```text
/mnt/raid/media/
    Videos/                     owner:group = root:plex (2775, setgid)
        Movies/                 owner:group = root:plex (2775, setgid)
        TV Shows/               owner:group = root:plex (2775, setgid)
    Downloads/                  owner:group = rdtclient:mediadl (2775, setgid)
        radarr/                 owner:group = rdtclient:mediadl (2775, setgid)
        sonarr/                 owner:group = rdtclient:mediadl (2775, setgid)
```

| User | Groups | Why |
|---|---|---|
| rdtclient | mediadl, plex | Downloads files; needs mediadl for downloads, plex for media library |
| radarr | mediadl, plex | Reads downloads to import; writes to media library |
| sonarr | mediadl, plex | Reads downloads to import; writes to media library |
| prowlarr | mediadl | Does not directly access files, but included for consistency |
| plex | mediadl | Reads media library; mediadl for any direct download access |
| tautulli | mediadl | Included for consistency |
| your user | mediadl | Manage files without sudo |

## Verifying Permissions

Check which groups a user belongs to:

```bash
groups radarr
groups rdtclient
```

Check directory ownership and permissions:

```bash
ls -la /mnt/raid/media/
ls -la /mnt/raid/media/Downloads/
ls -la /mnt/raid/media/Videos/
```

Test that Radarr can access the downloads directory:

```bash
sudo -u radarr ls /mnt/raid/media/Downloads/radarr/
```

Test that Radarr can write to the media library:

```bash
sudo -u radarr touch /mnt/raid/media/Videos/Movies/test && sudo -u radarr rm /mnt/raid/media/Videos/Movies/test
```
