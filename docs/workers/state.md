# State — Parent Session Reference

Last updated: 2026-10-21 (Worker 7 complete — edge-vm live, media-vm app-only)

Paste this file into the parent session to re-sync without rediscovery.

---

## Infrastructure (fully operational)

| Host | IP (LAN) | IP (Tailscale) | SSH alias | Proxmox VM ID |
|---|---|---|---|---|
| edge-vm | 192.168.1.173 | 100.106.60.22 | `ssh edge-vm` | 102 |
| media-vm | 192.168.1.169 | 100.107.1.14 | `ssh media-vm` | 100 |
| gameservers-vm | 192.168.1.170 | 100.117.244.86 | `ssh gameservers-vm` | 101 |
| devbox-template | — (stopped) | — | — | 103 |
| devbox-1 | — (stopped) | 100.79.150.3 | `ssh devbox-1` | 104 |
| proxmox | 192.168.1.168 | 100.77.224.51 | `ssh proxmox` | host |
| truenas | 192.168.1.164 | 100.67.119.85 | `ssh truenas` | — |
| serverhub | 192.168.1.166 | none | `ssh serverhub` | — (decommissioned) |

SSH key: `~/.ssh/homelab` (ed25519) — deployed to all hosts.
Passwords in `~/.env` on Windows: `SSH_PASSWORD`, `CF_DDNS_TOKEN`.

---

## edge-vm Docker Stack — All Running

Compose file: `compose/docker-compose.edge.yml`
Deployed at: `/opt/edge/server-stack/compose/`

| Container | Status | Notes |
|---|---|---|
| nginx-proxy-manager | ✅ | All proxy hosts configured, DNS-01 certs for all hostnames |
| cloudflared | ✅ | Tunnel `8cd27584-...` connected, 4 connectors |
| cloudflare-ddns | ✅ | Updates every 5min, covers plex + game server subdomains |
| portainer-agent | ✅ | Port 9001, registered in Portainer on media-vm as environment "edge-vm" (ID 6) |

---

## media-vm Docker Stack — All Running

Profiles active: `media`, `indexers`, `cloudflare-bypass`

| Container | Status | Notes |
|---|---|---|
| plex | ✅ | Libraries loaded, software transcode only (no GPU yet) |
| overseerr | ✅ | Migrated to Seerr 3.5.0 (ghcr.io/seerr-team/seerr:latest), auto-migration ran on first boot |
| tautulli | ✅ | |
| calibre-web | ✅ | Books imported, inotify watcher active |
| sonarr | ✅ | Root: /mnt/media/tv |
| radarr | ✅ | Root: /mnt/media/movies, collections fixed |
| prowlarr | ✅ | Indexers synced, TPB working, 1337x disabled (upstream Byparr issue) |
| rdt-client | ✅ | Using NAS DB, FinishedAction=2 (deletes local files after import) |
| queue-cleaner | ✅ | DRY_RUN=false, new API keys |
| rdt-blocklist | ✅ | DRY_RUN=false, new API keys |
| scan-media | ✅ | ClamAV + Discord notifications on import (Worker 2 added this) |
| byparr + proxies | ✅ | TPB working, 1337x disabled |
| portainer | ✅ | docker.xransum.com, Tailscale-only, Let's Encrypt cert via DNS-01 |

Known issues (cosmetic, not blocking):
- Sonarr/Radarr indexer backoff warnings (auto-recover)
- Sonarr/Radarr Docker path warning for rdt-client (path mapping set, cosmetic)

---

## DNS — xransum.com

### CF Tunnel (orange cloud) → NPM → container
overseerr, sonarr, radarr, prowlarr, tautulli, rdt, calibre, npm

### CF Tunnel (orange cloud) → NPM → backend
overseerr, sonarr, radarr, prowlarr, tautulli, rdt, calibre, npm,
pelican (→ Pelican Panel on gameservers-vm:80),
gameservers-vm (→ Wings WebSocket on gameservers-vm:8080 via NPM)

### Grey cloud (direct A record — Tailscale-only or DDNS-managed)
- plex.xransum.com → home IP 96.231.19.234 → router → edge-vm:443 (NPM+SSL) + media-vm:32400 (direct)
- docker.xransum.com → 100.106.60.22 (edge-vm Tailscale IP → NPM → media-vm:9443)
- proxmox.xransum.com → 100.106.60.22 (edge-vm Tailscale IP → NPM → proxmox:8006)
- nas.xransum.com → 100.106.60.22 (edge-vm Tailscale IP → NPM → truenas:80)
- mc1, mc2, mc3, valheim, satisfactory, factorio → home IP (port forwards set up ✅)

### SRV records (Minecraft — players connect without specifying port)
- `_minecraft._tcp.mc1.xransum.com` → port 25565 (ATM10 v7.1)
- `_minecraft._tcp.mc2.xransum.com` → port 25566 (ATM10 v4.5)
- `_minecraft._tcp.mc3.xransum.com` → port 25567 (ATM9)

### Port forwards (router → gameservers-vm 192.168.1.170)
25565, 25566, 25567, 25568, 25569, 25570, 25571 TCP

### SSL
- All CF tunnel services: valid Let's Encrypt cert via Cloudflare (auto-managed by NPM on edge-vm)
- plex.xransum.com: Let's Encrypt cert via NPM DNS-01/Cloudflare on edge-vm (expires 2027-01-17)
- docker.xransum.com: Let's Encrypt cert via NPM DNS-01/Cloudflare on edge-vm (expires 2027-01-17)
- gameservers-vm.xransum.com: Let's Encrypt cert via NPM DNS-01/Cloudflare on edge-vm (expires 2027-01-17)
- proxmox.xransum.com: Let's Encrypt cert via NPM DNS-01/Cloudflare on edge-vm (expires 2027-01-19)
- nas.xransum.com: Let's Encrypt cert via NPM DNS-01/Cloudflare on edge-vm (expires 2027-01-19)

---

## TrueNAS (192.168.1.164)

- `media` pool: 2 x MIRROR vdevs, mixed capacity — 20TB total, ~39% used
  - mirror-0: 2 x 10.9TB (original)
  - mirror-1: 2 x 9.1TB (added 2026-10-21, ex-serverhub RAID pair WP025G10 + WP0268FG)
- Boot pool: 223.6GB SSD

---

## gameservers-vm (192.168.1.170)

- Proxmox VM 101: Ubuntu 24.04, 12 vCPU, 32GB RAM, 512GB NVMe
- Pelican Panel: https://pelican.xransum.com (Docker, SQLite)
- Wings: native systemd binary, HTTP on :8080, proxied via NPM on media-vm
- NAS backup mount: nas:/mnt/media/gameservers → /mnt/gameservers
- Wings config: `ignore_panel_config_updates: true` — if Panel node settings change,
  manually edit /etc/pelican/config.yml and restart Wings

### Game servers (all in /var/lib/pelican/volumes/)

| Server | Port | UUID prefix | Status |
|---|---|---|---|
| ATM10 v7.1 (NeoForge 21.1.234) | 25565 | 83c95aef | ✅ confirmed working |
| ATM10 v4.5 (NeoForge 21.1.191) | 25566 | a4595ff6 | migrated, not started |
| ATM9 v0.3.2 (Forge 1.20.1) | 25567 | 5f275f4a | migrated, not started |
| ATM9 Sky v1.1.1 (Forge 1.20.1) | 25568 | 39d94756 | migrated, not started |
| BMC4 v30.5 (Forge 1.20.1) | 25569 | 1b557ac4 | migrated, not started |
| DH + Iris v0.4.2 (Fabric) | 25570 | 1ca0348a | migrated, not started |
| Sky Factory 4 v4.2.4 (Forge 1.12.2) | 25571 | 276e9e1c | migrated, not started |

---

## Serverhub (192.168.1.166)

- **FULLY DECOMMISSIONED** ✅ — drives pulled 2026-10-21, physically dead
- RTX 2070 moved to Proxmox ✅
- All data either migrated or confirmed nukable (MC servers in Pelican, Steam worlds discarded)
- All HDDs wiped and sorted: 9.1TB pair → TrueNAS, remainder trashed or on retainer

---

## Worker Status

| Worker | Status | Notes |
|---|---|---|
| Worker 1 — GPU Passthrough | ✅ completed | RTX 2070 in Proxmox, passthrough to media-vm, NVENC active in Plex |
| Worker 2 — Decommission | ✅ completed | Serverhub powered off, all services fixed |
| Worker 3 — Game Servers | ✅ completed | gameservers-vm live, Pelican+Wings deployed, 7 MC servers migrated |
| Worker 4 — Devboxes | ✅ completed | devbox-template (VM 102) live as Proxmox template, Tailscale up |
| Worker 5 — Portainer | ✅ completed | docker.xransum.com live, Tailscale-only |
| Worker 6 — Seerr Migration | ✅ completed | Seerr 3.5.0 live at overseerr.xransum.com, data intact |
| Worker 7 — edge-vm | ✅ completed | edge-vm live, all ingress moved off media-vm, proxmox+nas portless URLs working |

---

## Repo

GitHub: `https://github.com/xransum/server-stack`
Local: `~/Documents/Gits/server-stack` (Windows)
media-vm: `/opt/media/server-stack`
edge-vm: `/opt/edge/server-stack`
