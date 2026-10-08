# Migration Runbook

Step-by-step cutover from native systemd on `serverhub` to the Docker
Compose stack on Proxmox. Each phase is independently checkable. Do not
skip ahead — later phases depend on earlier ones being verified.

**Current state:** everything runs as native systemd on `serverhub`
(192.168.1.166, Debian 12). Target: NAS + Proxmox compute, with the media
stack running in `media-vm` via Docker Compose and game servers running in
`gameservers-vm` under Pelican (Panel + Wings). See `docs/proxmox-compute.md`.

---

## Pre-migration checklist

Before starting any phase, confirm:

- [ ] All services healthy on `serverhub` (`sudo systemctl status radarr sonarr prowlarr rdt-client byparr 'byparr-proxy@*'`)
- [ ] Prowlarr indexer Test passing for all active indexers
- [ ] Sonarr/Radarr import queue empty (no pending items mid-import)
- [ ] Game servers enumerated fresh (see Phase 5.5.1) -- capture the current
      list of game users, world/save dirs, and systemd units so Phase 5.5 has
      an up-to-date checklist (the inventory may have grown since this doc was
      written)
- [ ] Latest commits pulled on `serverhub` (`git pull` in `~/Documents/Gits/server-stack`)

---

## Phase 1 — Build the NAS

### 1.1 Assemble hardware

See `docs/homelab-hardware.md` for the full BOM. Key requirements:

- LSI HBA in IT mode (not IR mode) — ZFS requires passthrough, not RAID
- SAS expander wired to HBA (routes all bays through single HBA)
- Boot drive is a small SSD/NVMe — not USB flash (unreliable under constant writes)
- UPS connected before first power-on (ZFS pool corruption on sudden power loss)

### 1.2 Install TrueNAS Scale

1. Download TrueNAS Scale ISO from truenas.com
2. Write to USB with balenaEtcher or Rufus
3. Boot NAS from USB, install to boot SSD (not a data drive)
4. Initial setup: set admin password, configure network (static IP recommended,
   e.g. `192.168.1.167`)
5. Verify IPMI is accessible if the motherboard has it (Supermicro X10SRi-F does)

### 1.3 Create ZFS pools

In TrueNAS UI → Storage → Create Pool:

**media pool** (add drives in pairs as budget allows):
- Topology: Mirror
- Starting drives: 2× 12TB
- Dataset layout:
  ```
  media/
    downloads/complete
    downloads/incomplete
    movies
    tv
    music
    audiobooks
    books
  ```
- Snapshot schedule: daily on `media/movies`, `media/tv`; none on `media/downloads`

**s3 pool:**
- Topology: Mirror
- Drives: 2× 12TB
- Dataset: `s3/store`

**dev pool:**
- Topology: Mirror
- Drives: 2× 2TB
- Datasets: `dev/gameservers`, `dev/backups`, `dev/vmdisks`

See `docs/storage.md` for full rationale and expansion procedure.

### 1.4 Configure NFS exports

TrueNAS UI → Sharing → NFS → Add:

| Path | Hosts (restrict to) | Options |
|---|---|---|
| `/mnt/media` | media-vm IP | `rw, sync` |
| `/mnt/s3` | media-vm IP | `rw, sync` |
| `/mnt/dev` | Proxmox host IP | `rw, sync` |

Enable NFS service: TrueNAS UI → Services → NFS → Start Automatically → toggle on.

### 1.5 Verify NAS from a test machine

```bash
# From any Linux machine on the LAN
showmount -e nas-ip
mount -t nfs nas-ip:/mnt/media /tmp/test-mount
ls /tmp/test-mount
umount /tmp/test-mount
```

---

## Phase 2 — Build the Proxmox compute server

### 2.1 Install Proxmox VE

1. Download Proxmox VE ISO from proxmox.com
2. Install to NVMe (the ZFS-mirror pair — Proxmox installer can create this)
3. Remove enterprise repo, add community repo:
   ```bash
   sed -i 's|enterprise.proxmox.com|download.proxmox.com/debian/pve|' /etc/apt/sources.list.d/pve-enterprise.list
   echo "deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription" > /etc/apt/sources.list.d/pve-community.list
   apt update
   ```
4. Static IP recommended (e.g. `192.168.1.168`)
5. Access Proxmox UI at `https://proxmox-ip:8006`

### 2.2 Enable IOMMU (GPU passthrough prerequisite)

```bash
# Edit /etc/default/grub
# AMD CPU:
GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on iommu=pt"
# Intel CPU:
GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"

update-grub

# Add to /etc/modules
echo "vfio" >> /etc/modules
echo "vfio_iommu_type1" >> /etc/modules
echo "vfio_pci" >> /etc/modules
echo "vfio_virqfd" >> /etc/modules

reboot
```

Verify: `dmesg | grep -e DMAR -e IOMMU` should show IOMMU enabled.

### 2.3 Create media-vm

In Proxmox UI → Create VM:

| Setting | Value |
|---|---|
| Name | `media-vm` |
| OS | Debian 12 ISO |
| Machine | q35 |
| BIOS | OVMF (UEFI) |
| Disk | 64 GB on local NVMe storage |
| CPU | 8–12 cores (adjust per physical host spec) |
| RAM | 16–32 GB |
| Network | VirtIO bridge |

Install Debian 12 minimal, set static IP (e.g. `192.168.1.169`).

Add NVIDIA 2070 passthrough after VM is created (see `docs/proxmox-compute.md`
GPU passthrough section).

### 2.4 Create gameservers-vm

Same as above but:
- Name: `gameservers-vm`
- CPU: 8–12 cores
- RAM: 16–32 GB (32 GB recommended if running multiple game servers)
- Disk: 32 GB
- No GPU passthrough

### 2.5 Install Docker in both VMs

```bash
# Run inside each VM
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out and back in, then verify:
docker run hello-world
```

### 2.6 Mount NFS shares in media-vm

```bash
sudo apt install nfs-common

# Add to /etc/fstab
nas-ip:/mnt/media  /mnt/media  nfs  rw,sync,hard,intr,rsize=131072,wsize=131072,noatime  0 0
nas-ip:/mnt/s3     /mnt/s3     nfs  rw,sync,hard,intr  0 0

sudo mkdir -p /mnt/media /mnt/s3
sudo mount -a

# Verify
ls /mnt/media/
```

### 2.7 Mount NFS shares in gameservers-vm

```bash
sudo apt install nfs-common

# Add to /etc/fstab
nas-ip:/mnt/dev/gameservers  /mnt/gameservers  nfs  rw,sync,hard,intr  0 0

sudo mkdir -p /mnt/gameservers
sudo mount -a
```

### 2.8 Install Tailscale on both VMs and NAS

```bash
# On each VM:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

For TrueNAS: Apps → Available Applications → Tailscale → Install.

Verify all nodes appear in your Tailscale admin console and are reachable
by hostname.

---

## Phase 3 — Deploy the media stack

### 3.1 Clone the repo on media-vm

```bash
git clone https://github.com/xransum/server-stack.git ~/server-stack
cd ~/server-stack
```

### 3.2 Install NVIDIA container toolkit

See `docs/proxmox-compute.md` GPU passthrough section for full steps.
Verify with:

```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### 3.3 Create custom-definitions directory

```bash
mkdir -p ~/server-stack/compose/custom-definitions
cp ~/server-stack/definitions/*.yml ~/server-stack/compose/custom-definitions/
```

### 3.4 Create .env from example

```bash
cp ~/server-stack/compose/.env.example ~/server-stack/compose/.env
# Edit .env: fill in PLEX_CLAIM, API keys, etc.
nano ~/server-stack/compose/.env
```

### 3.5 Bring up the services that can start empty

Do not start Plex, Tautulli, or Overseerr yet. Their Docker volumes must be
populated first so they do not generate fresh state that conflicts with the
migrated data.

```bash
cd ~/server-stack
docker compose -f compose/docker-compose.yml \
  --profile indexers \
  --profile cloudflare-bypass \
  up -d
```

Verify all containers are running:

```bash
docker compose -f compose/docker-compose.yml ps
```

Check logs for any startup errors:

```bash
docker compose -f compose/docker-compose.yml logs --tail=50 sonarr
docker compose -f compose/docker-compose.yml logs --tail=50 prowlarr
```

---

## Phase 4 — Migrate config from serverhub

### 4.1 Export Sonarr database

```bash
# On serverhub — stop Sonarr first to ensure clean copy
sudo systemctl stop sonarr
scp -r serverhub:/var/lib/sonarr/. /tmp/sonarr-backup/

# On media-vm — copy into the sonarr-config Docker volume
docker cp /tmp/sonarr-backup/. sonarr:/config/
docker restart sonarr
```

Repeat for Radarr (`/var/lib/radarr`), Prowlarr (`/var/lib/prowlarr`),
and rdt-client.

> **Note:** SQLite database files carry absolute paths in some settings.
> After import, verify download client paths and root folder paths inside
> each service's UI point to `/mnt/media/...` (same path as the NFS mount).

### 4.2 Migrate Tautulli

```bash
# Tautulli — config and watch history database
sudo systemctl stop tautulli
sudo rsync -avh \
  /opt/Tautulli/ \
  nas-ip:/mnt/dev/backups/tautulli-migration-$(date +%F)/

# On media-vm
docker volume create server-stack_tautulli-config
docker volume inspect server-stack_tautulli-config --format '{{ .Mountpoint }}'
sudo rsync -avh \
  nas-ip:/mnt/dev/backups/tautulli-migration-$(date +%F)/ \
  /var/lib/docker/volumes/server-stack_tautulli-config/_data/
docker compose -f compose/docker-compose.yml --profile media up -d tautulli
```

After Plex is also running on `media-vm`, reconnect Tautulli to the new Plex
instance: Settings → Plex Media Server → update host to `media-vm-ip`.
Plex uses host networking in Docker Compose, so it is not reachable at a
bridge-network service name.

### 4.3 Migrate Overseerr

Overseerr is installed as a snap on `serverhub`, not a native binary. The
config lives at a non-standard path.

```bash
# Backup from serverhub
sudo rsync -avh \
  /var/snap/overseerr/current/ \
  nas-ip:/mnt/dev/backups/overseerr-migration-$(date +%F)/

# On media-vm — copy into Docker volume before starting container
docker volume create server-stack_overseerr-config
docker volume inspect server-stack_overseerr-config --format '{{ .Mountpoint }}'
sudo rsync -avh \
  nas-ip:/mnt/dev/backups/overseerr-migration-$(date +%F)/ \
  /var/lib/docker/volumes/server-stack_overseerr-config/_data/
docker compose -f compose/docker-compose.yml --profile media up -d overseerr
```

After starting the container, verify Sonarr/Radarr connections point at their
Docker service names and the Plex connection points at `media-vm-ip:32400`
because Plex uses host networking.

### 4.4 Migrate ClamAV

ClamAV stays native on `media-vm` rather than running in Docker.

```bash
# Copy config from serverhub to media-vm
scp serverhub:/etc/clamav/clamd.conf /etc/clamav/clamd.conf
scp serverhub:/etc/clamav/freshclam.conf /etc/clamav/freshclam.conf

# Install on media-vm
sudo apt install clamav clamav-daemon -y
sudo freshclam
sudo systemctl enable --now clamav-daemon clamav-freshclam
```

### Verify Prowlarr custom definitions

Confirm the byparr-proxy indexers are loaded:
- Prowlarr UI → Indexers — "1337x (via Byparr)" and "ThePirateBay (via Byparr)"
  should appear.
- Run Test on each. Should pass (byparr-proxy and byparr containers are running).

---

## Phase 4.5 — Migrate Plex data

Plex watch history, user profiles, ratings, playlists, and metadata all
live in the Plex data directory. This must be copied before starting the
new Plex container. If the new container starts fresh without this data,
all 15+ user profiles lose their watch history permanently.

**Total data size:** ~12GB

### Stop Plex on serverhub

```bash
sudo systemctl stop plexmediaserver
```

Do not restart Plex on serverhub after this point.

### Database notes

The key Plex library databases live here:

```text
/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/
```

- `com.plexapp.plugins.library.db`
- `com.plexapp.plugins.library.blobs.db`

These databases use a custom ICU collation compiled into Plex's own SQLite
binary. The system `sqlite3` binary cannot run `PRAGMA integrity_check` on
them and will throw `no such collation sequence: icu_root`. That is expected
and is not a corruption indicator. If Plex starts cleanly, the databases are
healthy.

### Copy Plex data to NAS as backup

```bash
sudo rsync -avh --progress \
  "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/" \
  nas-ip:/mnt/dev/backups/plex-migration-$(date +%F)/
```

### Copy Plex data into Docker volume path on media-vm

```bash
# On media-vm, create and inspect the Docker volume mountpoint
docker volume create server-stack_plex-config
docker volume inspect server-stack_plex-config --format '{{ .Mountpoint }}'
# Typical output: /var/lib/docker/volumes/server-stack_plex-config/_data

# Copy from NAS backup into the volume (before starting the container)
sudo rsync -avh --progress \
  nas-ip:/mnt/dev/backups/plex-migration-$(date +%F)/ \
  /var/lib/docker/volumes/server-stack_plex-config/_data/
```

### Start Plex container

```bash
docker compose -f compose/docker-compose.yml --profile media up -d plex
```

Plex will read the existing database on startup and verify it internally.
Watch logs for errors:

```bash
docker compose -f compose/docker-compose.yml logs -f plex
```

### Update library paths after Phase 5 completes

The migrated database still references old media paths
(`/mnt/raid0/media/Videos/Movies/` etc). After the Phase 5 rsync is finished,
update in Plex UI:

1. Plex Web UI → Settings → Libraries → Movies → Edit → change path to `/mnt/media/movies`
1. Libraries → TV Shows → Edit → change path to `/mnt/media/tv`
1. Libraries → Music → change to `/mnt/media/music`
1. Libraries → Books → change to `/mnt/media/books`
1. Scan all libraries. Verify counts match `serverhub`:
   - Movies: 431 items
   - TV Shows: 10,411 items

### Verify user profiles

Have one or two users confirm their watch history and continue watching
lists are intact before decommissioning `serverhub`.

---

## Phase 5 — Migrate media data from serverhub

> WARNING: The media drive on serverhub (`/dev/md0`) is 95% full — 8.2TB used
> of 9.1TB with only 448GB free. Phase 5 (media rsync to NAS) is time-sensitive.
> Do not add new media to serverhub after starting the migration. All new
> requests should be paused in Overseerr until the NAS is confirmed healthy.

### 5.1 rsync media library to NAS

Run from serverhub (or any machine that can reach both):

```bash
# Dry run first — verify what would be transferred
rsync -avhn --progress /mnt/raid0/media/Videos/     nas-ip:/mnt/media/
rsync -avhn --progress /mnt/raid0/media/Audio/      nas-ip:/mnt/media/music/
rsync -avhn --progress /mnt/raid0/media/Ebooks/     nas-ip:/mnt/media/books/
rsync -avhn --progress /mnt/raid0/media/Downloads/  nas-ip:/mnt/media/downloads/

# Live run (may take hours depending on library size)
rsync -avh --progress /mnt/raid0/media/Videos/     nas-ip:/mnt/media/
rsync -avh --progress /mnt/raid0/media/Audio/      nas-ip:/mnt/media/music/
rsync -avh --progress /mnt/raid0/media/Ebooks/     nas-ip:/mnt/media/books/
rsync -avh --progress /mnt/raid0/media/Downloads/  nas-ip:/mnt/media/downloads/
```

> Keep serverhub running during the rsync. Services stay up. The goal is
> to have the data on NAS before cutting services over, not a hard cutover.

### 5.2 Final rsync (catch any changes during migration)

Once the initial rsync finishes, run again with `--delete` to catch any
new files added while it ran:

```bash
rsync -avh --progress --delete /mnt/raid0/media/Videos/     nas-ip:/mnt/media/
rsync -avh --progress --delete /mnt/raid0/media/Audio/      nas-ip:/mnt/media/music/
rsync -avh --progress --delete /mnt/raid0/media/Ebooks/     nas-ip:/mnt/media/books/
rsync -avh --progress --delete /mnt/raid0/media/Downloads/  nas-ip:/mnt/media/downloads/
```

### 5.3 Verify media on NAS

```bash
# From media-vm (NFS mounted)
ls -lh /mnt/media/movies/ | head -20
ls -lh /mnt/media/tv/ | head -20
ls -lh /mnt/media/music/ | head -20
ls -lh /mnt/media/books/ | head -20
```

Now complete the Plex library path update described in Phase 4.5:

1. Plex Web UI → Settings → Libraries → Movies → Edit → change path to `/mnt/media/movies`
1. Libraries → TV Shows → Edit → change path to `/mnt/media/tv`
1. Libraries → Music → change to `/mnt/media/music`
1. Libraries → Books → change to `/mnt/media/books`
1. Scan all libraries. Verify counts match `serverhub`:
   - Movies: 431 items
   - TV Shows: 10,411 items

### 5.4 Smoke test the full pipeline

1. In Overseerr, request a test movie
1. Confirm Radarr picks it up and searches via Prowlarr
1. Confirm rdt-client receives the torrent and starts downloading
1. Confirm the download appears at `/mnt/media/downloads/complete/`
1. Confirm Radarr imports it to `/mnt/media/movies/`
1. Confirm Plex picks it up

---

## Phase 5.5 -- Migrate game servers

Game servers move to `gameservers-vm` under **Pelican** (Panel + Wings). They
are **on-demand**: started manually when wanted, never all at once (not enough
RAM to run them concurrently). This phase stages every existing world off
`serverhub`, re-creates each game as a Pelican server, and imports the worlds.

> **Inventory-agnostic by design.** Do NOT rely on any hardcoded list in this
> doc. New servers may be added to `serverhub` right up until the final build.
> Every step below **enumerates what is actually on the box at migration time**
> and copies whole home trees wholesale, so anything added between now and
> cutover is captured automatically.

Game-server data lives under per-user home directories on `serverhub` (one
system user per engine, e.g. `minecraft`, `steam`). In the new build, live
worlds run on the `gameservers-vm` local disk (Wings data dir) and Pelican
pushes scheduled backups to the NAS `dev/gameservers` dataset. The NAS copy is
both the migration staging area and the ongoing backup target.

### 5.5.1 Snapshot the live inventory (do this at cutover, not before)

```bash
# On serverhub -- discover every game-server user and what they hold.
# Adjust the user list if you have added engines beyond these.
for u in minecraft steam; do
  echo "===== $u ====="
  getent passwd "$u" >/dev/null || { echo "  (no such user, skip)"; continue; }
  home=$(getent passwd "$u" | cut -d: -f6)
  sudo du -sh "$home" 2>/dev/null
  sudo du -sh "$home"/*/ 2>/dev/null
done

# Discover every game-related systemd unit (templates, instances, enabled state)
systemctl list-unit-files | grep -iE 'minecraft|steam|valheim|rust|palworld|satisfactory|factorio|7days|ark|game'
# Capture which instances have ever been started (template units)
systemctl list-units --all | grep -iE 'minecraft@|game'
```

Save that output alongside the migration -- it is the checklist you verify
against on the other side.

### 5.5.2 Stage all game-server data to the NAS (wholesale)

Copy the **entire** home tree for each game user to the NAS staging area. This
guarantees worlds, configs, mods, server.properties, allowlists, and anything
else come along -- no per-game cherry-picking that could miss a file.

```bash
# On serverhub -- one rsync per game user, whole home tree.
for u in minecraft steam; do
  getent passwd "$u" >/dev/null || continue
  home=$(getent passwd "$u" | cut -d: -f6)
  sudo rsync -avh --progress "$home"/ \
    nas-ip:/mnt/dev/gameservers/_staging/"$u"/
done
```

Optional: Steam game binaries are re-downloadable via `steamcmd`, so you may
exclude `Steam/` and `steamcmd/` to save space and time
(`--exclude 'Steam/' --exclude 'steamcmd/'`). Keep every game's `Saved/`,
world, and config directories. When in doubt, copy it all -- storage is
cheaper than a lost world.

### 5.5.3 Stand up Pelican on gameservers-vm

1. Deploy the Pelican **Panel** + **Wings** stack on `gameservers-vm` (see
   `compose/docker-compose.gameservers.yml`).
2. Confirm the Panel is reachable (`pelican.xransum.com` via CF Tunnel -> Nginx
   PM) and that Wings registers as a node.
3. Install/confirm the eggs for each engine you are migrating (Minecraft Java
   PaperMC, Minecraft Bedrock, Valheim, Palworld, CS2, plus any existing
   serverhub engine such as Rust, Satisfactory, Factorio, 7 Days to Die).

### 5.5.4 Create servers and import worlds

For each world surfaced in 5.5.1:

1. Create a Pelican server from the matching egg, with its per-game RAM/CPU
   limits (see `compose/docker-compose.gameservers.yml` for starting
   estimates).
2. Import the staged files into the new server's data directory using the
   Pelican file manager or SFTP (host/port shown in the server's Settings),
   pulling from `/mnt/gameservers/_staging/<user>/<game>/`. Map each engine's
   world/save/config paths to the egg's expected layout.
3. Configure a Pelican **backup schedule** for the server targeting
   `/mnt/gameservers` so ongoing worlds are backed up to the NAS.

### 5.5.5 Verify nothing was missed

- Cross-check every user dir and every world/save dir from the 5.5.1 snapshot
  against the servers created in Pelican -- confirm each one has a server and
  its files imported with matching sizes.
- Start one world per engine via the Panel, confirm it loads with existing
  progress, then stop it. Do not leave them running -- they are on-demand.

---

## Phase 6 — External access cutover

### 6.1 Cloudflare Tunnel (public subdomains)

1. Cloudflare Zero Trust dashboard → Tunnels → Create tunnel
2. Name it (e.g. `homelab`)
3. Copy the tunnel token to `compose/.env` as `CLOUDFLARE_TUNNEL_TOKEN`
4. Start the proxy profile:
   ```bash
   docker compose -f compose/docker-compose.yml --profile proxy up -d nginx-proxy-manager cloudflared
   ```
5. In Nginx PM, create proxy hosts for:
   - `plex.xransum.com` → `http://media-vm-ip:32400`
   - `overseerr.xransum.com` → `http://overseerr:5055`
6. In CF Zero Trust → Tunnels → your tunnel → Public Hostnames → Add:
   - `plex.xransum.com` → `http://nginx-proxy-manager:80`
   - `overseerr.xransum.com` → `http://nginx-proxy-manager:80`
7. Verify `https://plex.xransum.com` and `https://overseerr.xransum.com`
   load from an external network

### 6.2 Nginx PM (internal subdomain routing)

Use Nginx PM for all internal service routing on `xransum.com`.

1. Access the admin UI at `http://media-vm-tailscale-ip:81`
1. Create proxy hosts for the internal services documented in
   `docs/proxmox-compute.md`:
   - `sonarr.xransum.com`
   - `radarr.xransum.com`
   - `prowlarr.xransum.com`
   - `tautulli.xransum.com`
   - `rdt.xransum.com`
   - `nas.xransum.com`
   - `proxmox.xransum.com`
   - `npm.xransum.com`
1. Use a Let's Encrypt wildcard cert for `*.xransum.com` via Cloudflare DNS-01
   for the Tailscale-only hosts

---

## Phase 7 — Smoke tests and verification

Run these from a device **off your LAN** (phone on mobile data, laptop at coffee shop):

- [ ] Tailscale connected → SSH to `devbox` hostname works
- [ ] Tailscale connected → `https://sonarr.xransum.com` loads
- [ ] Tailscale connected → `https://prowlarr.xransum.com` loads, indexer Tests pass
- [ ] `https://plex.xransum.com` loads (CF Tunnel, no Tailscale needed)
- [ ] `https://overseerr.xransum.com` loads (CF Tunnel, no Tailscale needed)
- [ ] Request a test item in Overseerr → full pipeline runs → Plex picks it up
- [ ] Byparr proxy: `curl http://media-vm-ts-ip:8882/precompiled/data_top100_recent.json` returns JSON

---

## Phase 8 — Decommission serverhub

Only after Phase 7 is fully verified.

```bash
# On serverhub — stop and disable all services
sudo systemctl disable --now radarr sonarr prowlarr rdt-client byparr \
  'byparr-proxy@*' byparr-unwrap queue-cleaner.timer

# Verify nothing is still running
sudo systemctl status radarr sonarr prowlarr

# Optional: remove binaries
sudo rm -rf /opt/Radarr /opt/Sonarr /opt/Prowlarr /opt/byparr \
  /opt/byparr-proxy /opt/byparr-unwrap /opt/rdt-client
```

Keep `serverhub` powered on for a few weeks as a cold fallback before
physically repurposing or decommissioning it.

> **Do not delete any game-server home directory** (`/home/minecraft`,
> `/home/steam`, and any other game users surfaced in Phase 5.5.1) until Phase
> 5.5.5 is verified on `gameservers-vm` -- every world loads with existing
> progress under Pelican. The commands above only touch the media stack under
> `/opt`; game-server saves live under `/home` and must be confirmed migrated
> first.

---

## Rollback procedure

If any phase fails and you need to revert to serverhub:

```bash
# On serverhub — restart all native services
sudo systemctl start radarr sonarr prowlarr rdt-client byparr \
  'byparr-proxy@*' byparr-unwrap

# On media-vm — stop Docker stack
docker compose -f compose/docker-compose.yml down
```

Since serverhub and the NAS run independently, rolling back is safe at
any point before Phase 8. The media data on NAS is read-only from
serverhub's perspective after the rsync — serverhub's local copy remains
intact until explicitly removed.

---

## Security notes

- **Cloudflare Tunnel token**: The old `serverhub` `cloudflared` service file
  contains the tunnel token in plain text at
  `/etc/systemd/system/cloudflared.service`. Generate a new tunnel token for
  `media-vm` in the Cloudflare Zero Trust dashboard. Revoke the old token
  after the new tunnel is confirmed working. Never reuse the old token.

- **API keys**: Regenerate all API keys after migration. Do not copy
  `serverhub` API keys into the new stack.
  - Radarr: Settings → General → Security → API Key → Regenerate
  - Sonarr: same path
  - Prowlarr: same path
  - Update `queue-cleaner.env` and decluttarr environment with the new keys.

- **queue-cleaner.env**: Contains API keys in plain text. Never commit this
  file to git. Store only at `/etc/queue-cleaner.env` on the server with
  `chmod 600 /etc/queue-cleaner.env`. The repo `.gitignore` should exclude
  runtime `*.env` files.

- **Cloudflare Tunnel token in .env**: The `compose/.env` file will contain
  `CLOUDFLARE_TUNNEL_TOKEN`. Verify this file stays out of git and is never
  committed.
