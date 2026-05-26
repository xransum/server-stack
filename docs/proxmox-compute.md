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

| VM | vCPU | RAM | Local disk | GPU | Purpose |
|---|---|---|---|---|---|
| `media-vm` | 8–12 cores | 16–32 GB | 64 GB (OS + Docker layers) | NVIDIA 2070 (PCIe PT) | Full media stack: Plex, Sonarr, Radarr, Prowlarr, rdt-client, Overseerr, Tautulli, byparr, byparr-proxy, byparr-unwrap, reverse proxy, S3 service |
| `gameservers-vm` | 8–12 cores | 16–32 GB | 32 GB (OS + Docker layers) | None | Game server stack: Pterodactyl Panel + per-game containers. On-demand, not always running. |
| `devbox-template` | 4 cores | 8 GB | 32 GB (thin provisioned) | None | Never run directly. Template for linked clones. |
| `devbox-N` | 4 cores | 8 GB | Thin clone | None | SSH over Tailscale. Spin up/tear down on demand. |

> **Note:** vCPU and RAM numbers above are starting points. The actual
> allocation depends on the physical host spec, which is not yet finalized.
> See open decisions below. Size conservatively at first — Proxmox lets you
> hot-add vCPUs and RAM to running VMs (with some caveats).

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
   GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on iommu=pt"
   # or for Intel:
   GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"
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

## Cloudflare Tunnel (Overseerr public access)

Install `cloudflared` as a Docker container in media-vm. Creates an outbound
tunnel from your network to Cloudflare's edge — no port forwarding, no public
IP exposure.

Only Overseerr (media request UI for friends/family) is exposed publicly.
Everything else stays Tailscale-only.

```bash
# After creating a tunnel in Cloudflare Zero Trust dashboard:
docker run -d --name cloudflared \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run \
  --token <your-tunnel-token>
```

In Cloudflare Zero Trust dashboard, add a public hostname:
`overseerr.yourdomain.com` → `http://localhost:5055`

---

## Open decisions

| Decision | Options | Notes |
|---|---|---|
| Compute server CPU/RAM | TBD | Affects vCPU allocation numbers in this doc. Update when hardware is chosen. |
| Reverse proxy | Caddy / Nginx Proxy Manager / Traefik / nginx | Internal routing for subdomain per service + wildcard TLS cert via Cloudflare DNS-01. Decide at lab time. |
| S3 service | Garage / MinIO / Nextcloud | Garage: lightweight Rust, S3 API, good for personal use. MinIO: industry standard, recent license drama. Nextcloud: better if the use case is "replace Dropbox" rather than S3 API compat. Decide at lab time. |
| WireGuard on MikroTik | Optional enhancement once MikroTik router deployed | RouterOS has built-in WireGuard. Could use as primary self-hosted VPN with Tailscale as fallback. Not required — Tailscale alone is sufficient. |
