# Proxmox Compute

Proxmox VE bare-metal host running the full service stack as isolated VMs.
All persistent data lives on the NAS (see `docs/storage.md`); the compute
server is intentionally stateless with respect to data.

---

## Why Proxmox + VMs (not bare-metal Docker, not LXC)

**Why not bare-metal Docker?**
A single OS running everything provides no isolation. A bad container,
a kernel panic from an NVIDIA driver issue, or a rogue game server can
take down Plex for everyone. VMs provide hard resource limits and fault
isolation.

**Why VMs over LXC?**
- NVIDIA GPU passthrough is straightforward with VMs (PCIe passthrough).
  It is significantly more complex and fragile with unprivileged LXC
  containers.
- Docker-in-LXC requires privileged mode or nested virtualization flags
  that create security surface area.
- VM snapshots are first-class in Proxmox and cover the entire system state,
  not just data.
- The migration target for the existing `server-stack` repo is Docker Compose,
  not LXC.

**Why Docker Compose inside the VMs?**
The existing stack already has systemd service definitions and the migration
path is to Docker Compose (see `compose/`). Running Docker inside a Debian VM
keeps the service definitions portable — the same `docker-compose.yml` can run
on any Docker host regardless of whether it's in Proxmox, on bare metal, or
in a cloud VM.

---

## VM layout

> **Compute server hardware (confirmed, pending DDR5 price drop):**
> - CPU: AMD Ryzen 9 7950X3D (AM5, 16c/32t, 5.7GHz boost, 3D V-Cache)
> - Motherboard: ASUS ProArt X870E-CREATOR WiFi
> - RAM: 64GB DDR5-6400 (waiting for price to drop from $829 to ~$450-500)
> - GPU: NVIDIA RTX 2070 (PCIe passthrough to media-vm, moved from serverhub)
> - Boot NVMe: WD Green SN350 250GB (Proxmox OS)
> - VM NVMe: Samsung 990 Pro 2TB (VM disk images)
> - Network: 10Gtek X550-AT2 10GbE NIC
> - PSU: Seasonic Focus GX-1000W ATX 3.1
>
> GPU passthrough: RTX 2070 supports NVENC for Plex (4-6 simultaneous 1080p
> transcodes). 4K HDR -> 1080p SDR tone-mapped transcodes are supported
> (Turing architecture). Plex Pass required for hardware transcoding.

| VM | vCPU | RAM | Local disk | GPU | Purpose |
|---|---|---|---|---|---|
| `media-vm` | 10 cores | 24 GB | 64 GB | RTX 2070 (PCIe PT) | Full media stack |
| `gameservers-vm` | 8 cores | 32 GB | 32 GB | None | Pterodactyl + game containers |
| `devbox-template` | 4 cores | 8 GB | 32 GB (thin) | None | Template only, never run directly |
| `devbox-N` | 4 cores | 8 GB | Thin clone | None | On-demand dev work |

> **Host allocation note:** these allocations assume the 7950X3D physical host
> (16c/32t total). Leave 2 cores and ~8GB unallocated on the host for Proxmox
> overhead.

> **Note on gameservers-vm RAM:** Some games are very RAM-hungry.
> Minecraft with mods: 6–12 GB per instance. Valheim/Palworld: 4–8 GB.
> CS2/Source: 2–4 GB. If running multiple game servers simultaneously,
> 32 GB RAM for this VM may be needed. See per-game resource notes in
> `compose/docker-compose.gameservers.yml`.

---

## NFS mounts inside VMs

Both VMs mount NFS shares from the NAS. Add to `/etc/fstab` inside each VM:

```
# media-vm
nas-ip:/mnt/media    /mnt/media    nfs    rw,sync,hard,intr,rsize=131072,wsize=131072,noatime    0 0
nas-ip:/mnt/s3       /mnt/s3       nfs    rw,sync,hard,intr    0 0

# gameservers-vm
nas-ip:/mnt/dev/gameservers    /mnt/gameservers    nfs    rw,sync,hard,intr    0 0
```

Use `hard,intr` (not `soft`) for NFS mounts. `soft` mounts silently return
EIO on timeout, which corrupts SQLite databases (Sonarr/Radarr/Prowlarr use
SQLite). `hard,intr` retries indefinitely but allows Ctrl-C to interrupt.

> **Important:** Do not put Sonarr/Radarr/Prowlarr database files on NFS.
> SQLite over NFS has locking issues and is slow. App config/DB goes on the
> local VM disk. Only media files and downloads go on NFS.

---

## NVIDIA 2070 PCIe passthrough (media-vm)

The 2070 currently in `serverhub` moves to the compute node and is passed
through to `media-vm` for Plex NVENC hardware transcoding.

### Proxmox host setup

1. Enable IOMMU in BIOS (AMD: SVM + IOMMU / Intel: VT-d + VT-x)
2. Add to `/etc/default/grub`:
   ```
   # AMD (7950X3D — confirmed hardware)
   GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on iommu=pt"
   ```
3. `update-grub && reboot`
4. Blacklist the NVIDIA driver on the Proxmox host (the GPU must not be
   claimed by the host — only the VM can use it):
   ```
   echo "blacklist nouveau" >> /etc/modprobe.d/blacklist.conf
   echo "blacklist nvidia" >> /etc/modprobe.d/blacklist.conf
   echo "options vfio-pci ids=<vendor:device>" >> /etc/modprobe.d/vfio.conf
   update-initramfs -u && reboot
   ```
   Get the vendor:device IDs with `lspci -nn | grep -i nvidia`.
5. In Proxmox UI: media-vm → Hardware → Add → PCI Device → select the 2070
   → check "All Functions" and "Primary GPU" if it's the only GPU.

### Inside media-vm

```bash
# Install NVIDIA drivers
sudo apt install nvidia-driver firmware-misc-nonfree

# Install NVIDIA container toolkit for Docker
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### Plex NVENC config

In Plex Media Server settings → Transcoder → check "Use hardware acceleration
when available". The Plex Docker container needs `--gpus all` or the compose
equivalent (see `compose/docker-compose.yml`). Requires an active Plex Pass
subscription for hardware transcoding.

The 2070 handles 4–6 simultaneous 1080p NVENC transcodes comfortably.
4K HDR → 1080p SDR (tone-mapped) transcodes require a Maxwell-gen or newer
GPU — the 2070 (Turing) supports this.

---

## Devbox workflow

Devboxes are for dev work — coding from phone (NeoServer) and laptop, both
on LAN and off-network over Tailscale.

### Creating the template VM

1. Create a new VM in Proxmox with Debian 12 (or your preferred distro)
2. Install base packages: `git curl vim tmux htop build-essential`
3. Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`
4. Configure SSH: key auth only, disable password auth
5. Snapshot: Proxmox UI → VM → Snapshots → Take Snapshot → name "clean-base"
6. Convert to template: right-click VM → Convert to Template

### Spinning up a devbox

```bash
# In Proxmox UI or via CLI
qm clone <template-id> <new-id> --name devbox-work --full 0

# Start it
qm start <new-id>

# SSH over Tailscale (once it registers)
ssh devbox-work.your-tailnet
```

### Archiving a devbox

```bash
# Snapshot current state before destroying
qm snapshot <id> final-state --description "done with this project"

# If you want to keep it on NAS for later restoration:
vzdump <id> --dumpdir /mnt/dev/backups/ --compress zstd

# Destroy
qm destroy <id>
```

### Restoring from archive

```bash
qm restore <new-id> /mnt/dev/backups/<archive>.vma.zst
qm start <new-id>
```

---

## Game server workflow (gameservers-vm)

Game servers run as Docker containers managed by Pterodactyl Panel.
World saves and persistent data mount from the NAS (`/mnt/gameservers/<game>/`)
so they survive container rebuilds.

Archive workflow:
1. Stop the game server container via Pterodactyl
2. On the NAS: take a ZFS snapshot of `dev/gameservers/<game>` dataset
3. Container can be deleted — world is safe on NAS
4. To restore: create new container, same NFS mount path, start

Per-game resource limits are defined in `compose/docker-compose.gameservers.yml`.
These are left as TODOs until each game is actually stood up — resource
requirements vary significantly per game and per modpack.

### Game server DNS and subdomain routing

Game server traffic splits into two categories with different routing:

**HTTP admin panels** (Pterodactyl Panel web UI) route through Cloudflare
Tunnel -> Nginx PM like all other HTTP services:

| Subdomain | Target | Access |
|---|---|---|
| `pterodactyl.xransum.com` | `gameservers-vm:80` | CF Tunnel |

**Raw TCP/UDP game ports** (actual game client connections) cannot go
through Cloudflare Tunnel (HTTP only). These use direct port forwards on
the FIOS gateway and grey cloud DNS records on `xransum.com`.

Grey cloud records: Cloudflare DNS set to DNS-only (grey cloud, not orange
cloud). The record resolves directly to your home IP. Your home IP is
visible to players — this is acceptable for a private server among friends.

| Subdomain | DNS type | Port | Forward to |
|---|---|---|---|
| `mc.xransum.com` | A, grey cloud | 25565 | Minecraft LXC/container IP |
| `mc2.xransum.com` | A, grey cloud | 25566 | Second Minecraft instance |
| `rust.xransum.com` | A, grey cloud | 28015 | Rust server container |

FIOS gateway port forward rules (add when a game server is active):

| External port | Internal IP | Internal port | Protocol |
|---|---|---|---|
| 25565 | gameservers-vm-ip | 25565 | TCP |
| 25566 | gameservers-vm-ip | 25566 | TCP |
| 28015 | gameservers-vm-ip | 28015 | TCP+UDP |
| 28016 | gameservers-vm-ip | 28016 | TCP (RCON) |

Grey cloud records contain your real home IP and must be kept updated when
the IP rotates. The `dns-updater` service handles this automatically via the
Cloudflare API. See `docs/dns-updater.md`. Only grey cloud records need
updating — orange cloud (proxied) subdomains are unaffected by IP changes.

Minecraft players connect using the subdomain directly. The standard
Minecraft SRV record approach works but is not required since the default
port 25565 is already used:

```dns
# Optional SRV record (if using non-standard port)
_minecraft._tcp.mc.xransum.com  SRV  0 5 25565  mc.xransum.com
```

---

## Tailscale setup

Install Tailscale on the Proxmox host, both VMs, and all devboxes.
Also install on NAS (TrueNAS has a Tailscale app in its catalog).

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

Once registered, every machine is reachable at `<hostname>.your-tailnet`
from any device with Tailscale running — phone (Tailscale iOS app +
NeoServer for SSH), laptop, anywhere in the world. No port forwarding.

For the NAS: TrueNAS Scale → Apps → Available Applications → Tailscale.

---

## Cloudflare Tunnel (public subdomains)

Install `cloudflared` as a Docker container in media-vm. Creates an outbound
tunnel from your network to Cloudflare's edge — no port forwarding, no public
IP exposure.

Plex and Overseerr are the only public services. Everything else stays
Tailscale-only.

```bash
# After creating a tunnel in Cloudflare Zero Trust dashboard:
docker run -d --name cloudflared \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run \
  --token <your-tunnel-token>
```

In Cloudflare Zero Trust dashboard, add public hostnames that point at Nginx PM:

- `plex.xransum.com` → `http://nginx-proxy-manager:80`
- `overseerr.xransum.com` → `http://nginx-proxy-manager:80`

---

## Subdomain routing

Public HTTP traffic routes through Cloudflare Tunnel -> Nginx PM -> the
internal service target. Most services use Docker DNS names. Plex is the
exception because it runs with `network_mode: host`, so Nginx PM must forward
to the `media-vm` host IP instead.

| Subdomain | Forward target | Port | Public? |
|---|---|---|---|
| `plex.xransum.com` | `media-vm-ip` | 32400 | CF Tunnel |
| `overseerr.xransum.com` | `overseerr` | 5055 | CF Tunnel |
| `sonarr.xransum.com` | `sonarr` | 8989 | Tailscale only |
| `radarr.xransum.com` | `radarr` | 7878 | Tailscale only |
| `prowlarr.xransum.com` | `prowlarr` | 9696 | Tailscale only |
| `tautulli.xransum.com` | `tautulli` | 8181 | Tailscale only |
| `rdt.xransum.com` | `rdt-client` | 6500 | Tailscale only |
| `nas.xransum.com` | `nas-ip` | 80 | Tailscale only |
| `proxmox.xransum.com` | `proxmox-ip` | 8006 | Tailscale only |
| `npm.xransum.com` | `nginx-proxy-manager` | 81 | Tailscale only |

Cloudflare Tunnel handles SSL termination for public subdomains. Nginx PM
handles SSL via a Let's Encrypt wildcard cert (Cloudflare DNS-01 challenge)
for Tailscale-accessible subdomains.

---

## Resolved decisions

| Decision | Resolution | Notes |
|---|---|---|
| Compute server CPU/RAM | Ryzen 9 7950X3D (AM5) + 64GB DDR5-6400 | Wait for the DDR5 kit to drop to the $450-500 target before ordering. |
| Reverse proxy | Nginx Proxy Manager (Nginx PM) | `jc21/nginx-proxy-manager` in `compose/docker-compose.yml`. |
| S3 service | MinIO on TrueNAS Scale | Native app backed by `/mnt/s3/store`; not part of the media-vm compose stack. |
| Network switch | Dumb 10GbE switch | TP-Link TL-SX1008 or equivalent. Keep the FIOS gateway for routing. |
| Domain | `xransum.com` | Homelab services live here. `kevin-haas.com` stays on GitHub Pages. |

## Open decisions

| Decision | Options | Notes |
|---|---|---|
| Per-game RAM/CPU limits | Per-game | TODOs in `compose/docker-compose.gameservers.yml`. Fill in when standing up each game. |
