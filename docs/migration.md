# Migration Runbook

Step-by-step cutover from native systemd on `serverhub` to the Docker
Compose stack on Proxmox. Each phase is independently checkable. Do not
skip ahead — later phases depend on earlier ones being verified.

**Current state:** everything runs as native systemd on `serverhub`
(192.168.1.166, Debian 12). Target: NAS + Proxmox compute, services
running in `media-vm` via Docker Compose.

---

## Pre-migration checklist

Before starting any phase, confirm:

- [ ] All services healthy on `serverhub` (`sudo systemctl status radarr sonarr prowlarr rdt-client byparr 'byparr-proxy@*'`)
- [ ] Prowlarr indexer Test passing for all active indexers
- [ ] Sonarr/Radarr import queue empty (no pending items mid-import)
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

### 3.5 Bring up the stack

```bash
cd ~/server-stack
docker compose -f compose/docker-compose.yml \
  --profile media \
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

### 4.2 Verify Prowlarr custom definitions

Confirm the byparr-proxy indexers are loaded:
- Prowlarr UI → Indexers — "1337x (via Byparr)" and "ThePirateBay (via Byparr)"
  should appear.
- Run Test on each. Should pass (byparr-proxy and byparr containers are running).

### 4.3 Smoke test the full pipeline

1. In Overseerr, request a test movie
2. Confirm Radarr picks it up and searches via Prowlarr
3. Confirm rdt-client receives the torrent and starts downloading
4. Confirm the download appears at `/mnt/media/downloads/complete/`
5. Confirm Radarr imports it to `/mnt/media/movies/`
6. Confirm Plex picks it up

---

## Phase 5 — Migrate media data from serverhub

### 5.1 rsync media library to NAS

Run from serverhub (or any machine that can reach both):

```bash
# Dry run first — verify what would be transferred
rsync -avhn --progress \
  /mnt/raid/media/Videos/ \
  nas-ip:/mnt/media/

# Live run (may take hours depending on library size)
rsync -avh --progress \
  /mnt/raid/media/Videos/ \
  nas-ip:/mnt/media/
```

> Keep serverhub running during the rsync. Services stay up. The goal is
> to have the data on NAS before cutting services over, not a hard cutover.

### 5.2 Final rsync (catch any changes during migration)

Once the initial rsync finishes, run again with `--delete` to catch any
new files added while it ran:

```bash
rsync -avh --progress --delete \
  /mnt/raid/media/Videos/ \
  nas-ip:/mnt/media/
```

### 5.3 Verify media on NAS

```bash
# From media-vm (NFS mounted)
ls -lh /mnt/media/movies/ | head -20
ls -lh /mnt/media/tv/ | head -20

# Check Plex can see the library
# Plex UI → Libraries → Movies → scan
```

---

## Phase 6 — External access cutover

### 6.1 Cloudflare Tunnel (Overseerr public access)

1. Cloudflare Zero Trust dashboard → Tunnels → Create tunnel
2. Name it (e.g. `homelab`)
3. Copy the tunnel token to `compose/.env` as `CLOUDFLARE_TUNNEL_TOKEN`
4. Start the proxy profile:
   ```bash
   docker compose -f compose/docker-compose.yml --profile proxy up -d cloudflared
   ```
5. In CF Zero Trust → Tunnels → your tunnel → Public Hostnames → Add:
   - Subdomain: `overseerr`
   - Domain: `yourdomain.com`
   - Service: `http://media-vm-tailscale-ip:5055`
6. Verify `https://overseerr.yourdomain.com` loads from external network

### 6.2 Reverse proxy (internal subdomain routing)

> **Open decision:** Caddy / Nginx Proxy Manager / Traefik / nginx.
> Complete this step once the reverse proxy choice is made.
> See `docs/proxmox-compute.md` open decisions.

After picking and deploying the reverse proxy, configure:

| Subdomain | Target |
|---|---|
| `plex.home` | `http://plex:32400` |
| `sonarr.home` | `http://sonarr:8989` |
| `radarr.home` | `http://radarr:7878` |
| `prowlarr.home` | `http://prowlarr:9696` |
| `overseerr.home` | `http://overseerr:5055` |

TLS via Cloudflare DNS-01 challenge for a wildcard cert (`*.home` or `*.yourdomain.com`).

---

## Phase 7 — Smoke tests and verification

Run these from a device **off your LAN** (phone on mobile data, laptop at coffee shop):

- [ ] Tailscale connected → SSH to `devbox` hostname works
- [ ] Tailscale connected → `http://sonarr.home` (or `http://media-vm-ts-ip:8989`) loads
- [ ] Tailscale connected → `http://prowlarr.home` loads, indexer Tests pass
- [ ] `https://overseerr.yourdomain.com` loads (CF Tunnel, no Tailscale needed)
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
