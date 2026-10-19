# State — Parent Session Reference

Last updated: 2026-10-19

Paste this file into the parent session to re-sync without rediscovery.

---

## Infrastructure (fully operational)

| Host | IP (LAN) | IP (Tailscale) | SSH alias |
|---|---|---|---|
| media-vm | 192.168.1.169 | 100.107.1.14 | `ssh media-vm` or `ssh media-vm-ts` |
| proxmox | 192.168.1.168 | 100.77.224.51 | `ssh proxmox` or `ssh proxmox-ts` |
| truenas | 192.168.1.164 | 100.67.119.85 | `ssh truenas` or `ssh truenas-ts` |
| serverhub | 192.168.1.166 | none | `ssh serverhub` (being decommissioned) |

SSH key: `~/.ssh/homelab` (ed25519) — deployed to all hosts.
Passwords in `~/.env` on Windows: `SSH_PASSWORD`, `CF_DDNS_TOKEN`.

---

## media-vm Docker Stack — All Running

Profiles active: `media`, `indexers`, `proxy`, `cloudflare-bypass`

| Container | Status | Notes |
|---|---|---|
| plex | ✅ | Libraries loaded, software transcode only (no GPU yet) |
| overseerr | ✅ | |
| tautulli | ✅ | |
| calibre-web | ✅ | Books imported, inotify watcher active |
| sonarr | ✅ | Root: /mnt/media/tv |
| radarr | ✅ | Root: /mnt/media/movies |
| prowlarr | ✅ | 7 indexers, Byparr stack running |
| rdt-client | ✅ | Using NAS DB at /mnt/media/downloads/rdtclient.db |
| queue-cleaner | ✅ | DRY_RUN=false |
| rdt-blocklist | ✅ | DRY_RUN=false |
| nginx-proxy-manager | ✅ | |
| cloudflared | ✅ | Tunnel connected |
| cloudflare-ddns | ✅ | Updates every 5min |
| byparr + proxies | ✅ | |

Known issues (cosmetic, not blocking):
- Sonarr/Radarr indexer backoff warnings (auto-recover)
- Sonarr/Radarr Docker path warning for rdt-client (path mapping set, cosmetic)

---

## DNS — xransum.com

### CF Tunnel (orange cloud) → NPM → container
overseerr, sonarr, radarr, prowlarr, tautulli, rdt, calibre, npm, proxmox, nas

### Grey cloud (direct A record, DDNS-managed by cloudflare-ddns on media-vm)
- plex.xransum.com → home IP 96.231.19.234 → router → media-vm:443 (NPM+SSL) + 32400 (direct)
- mc1, mc2, valheim, satisfactory, factorio → home IP (port forwards NOT YET set up — pending gameservers-vm)

### SSL
- All CF tunnel services: valid Let's Encrypt cert via Cloudflare (auto-managed)
- plex.xransum.com: Let's Encrypt cert via NPM DNS challenge (expires 2027-01-17)

---

## Serverhub (192.168.1.166)

- All services STOPPED
- Media files on local RAID — NAS is source of truth (sizes match)
- Pending decommission (Worker 2)
- RTX 2070 still installed here — needs to move to Proxmox (Worker 1 trigger)

---

## Worker Status

| Worker | Status | Notes |
|---|---|---|
| Worker 1 — GPU Passthrough | ⏳ pending | Trigger: RTX 2070 physically in Proxmox |
| Worker 2 — Decommission | ⏳ pending | Independent, anytime |
| Worker 3 — Game Servers | ⏳ pending | Independent, anytime |
| Worker 4 — Devboxes | ⏳ pending | Independent, anytime |

---

## Repo

GitHub: `https://github.com/xransum/server-stack`
Local: `~/Documents/Gits/server-stack` (Windows)
media-vm: `/opt/media/server-stack`
