# Session Log

Running log of significant decisions, discoveries, and session outcomes.
Purpose: future-you (or any agent picking up this repo) should be able to
skim this file and immediately understand what was done, why, and what was
deferred — without reading commit diffs or reverse-engineering the code.

Format: newest entry at the top.

---

## 2026-10-21 — Worker 8: Shelfmark ebook pipeline

**Context:** Deploy Shelfmark (on-demand ebook search/grab) on media-vm, expose at
`shelfmark.xransum.com`, wire into existing scan-media ClamAV sidecar and Calibre-Web
library.

**Done:**

- **Staging dirs** on TrueNAS: `downloads/books/incoming` (INGEST_DIR) and
  `downloads/books/ready` (calibre-web import staging). Both 777 so NFS root-squashed
  processes can delete files after import.
- **Shelfmark deployed** on media-vm, port 8084, `indexers` profile. Real-Debrid native
  client (qBittorrent mode silently fails — Shelfmark issues #470/#607).
- **post-process hook** (`scripts/shelfmark-post-process.py`) — scans via scan-media,
  moves clean files to `books/ready`. Script must be `chmod +x` (git mode 100755) or
  Shelfmark refuses to call it.
- **Calibre-web poller** replaced inotify watcher — inotify unreliable over NFS for
  cross-container writes. 15s poll loop is simple and reliable. Init script needs `+x`
  bit in git or s6 silently skips it at container boot.
- **NFS permissions** — `/import` (books/ready) was 755, preventing root (NFS-squashed
  to nobody) from deleting source files. Fixed to 777 on TrueNAS.
- **NPM proxy host** (edge-vm ID 16) + CF Tunnel config v9 + DNS CNAME created manually
  (token-managed tunnels don't auto-create DNS records).
- **Pipeline verified** end-to-end: Player of Games grabbed → scanned → moved → imported
  into Calibre → source deleted. DCC Books 1-8 added manually via TrueNAS file drop.
- **AudioBookBay custom Cardigann definition** added (`compose/custom-definitions/audiobookbay.yml`)
  — ebb.la SSL cert mismatch workaround. Audiobooks only, not epubs.
- **Prowlarr explicit DNS** (1.1.1.1/8.8.8.8) to avoid ISP SSL interception on indexers.
- **Calibre library mounted read-only** in shelfmark container for metadata.db dedup.

**Gotchas:**

- Ebook torrent coverage is genuinely thin — virtually all indexers with Books categories
  are CF/DDoS-Guard protected or have sparse content. Direct download sites (Z-Lib,
  Anna's Archive) are the real source but both have bot protection that blocks server IPs
  or requires manual CAPTCHA. z-lib.id works from media-vm; z-lib.gd IP-blocks server IPs.
- Anna's Archive uses DDoS-Guard — no bypasser can solve its manual CAPTCHA. Dead end.
- Manual epub import workflow: drop files into `books/ready` on TrueNAS, calibre-web
  poller imports within 15s. Practical for books not findable via automated sources.
- Docker bind mount inode mismatch: always `git pull` BEFORE `docker compose up` or
  force-recreate after pull, otherwise container sees old file content.

**Deferred:**

- Better ebook indexer coverage — no good automated solution found during this session.
  KAT via Byparr is theoretically possible (same pattern as 1337x) but book content
  on KAT is also thin. Left for a future session if needed.

---

## 2026-10-21 — Worker 7: edge-vm — dedicated ingress layer

**Context:** media-vm was doing double duty as application host and ingress layer (NPM + cloudflared + cloudflare-ddns). As the homelab grows with more VMs and services, a dedicated edge VM isolates all traffic routing from the application stack. Also enables portless URLs for proxmox.xransum.com and nas.xransum.com.

**Done:**

- **VM ID renumber** — devbox-1 (103→104), devbox-template (102→103) via LVM thin `lvrename` + conf file move on Proxmox. Full clone confirmed (disk references independent).
- **edge-vm provisioned** — Ubuntu 24.04, VM 102, 192.168.1.173, Tailscale 100.106.60.22. Bootstrap: Docker, Tailscale, passwordless sudo, homelab key. SSH aliases `edge-vm` / `edge-vm-lan` added to Windows SSH config.
- **`compose/docker-compose.edge.yml` created** — cloudflared, NPM, cloudflare-ddns. Same tunnel token as media-vm (one tunnel, one connector on edge-vm).
- **NPM volumes migrated** from media-vm → Windows → edge-vm. All proxy host backends updated from Docker service names to LAN IPs via NPM REST API (direct SQLite edits don't regenerate nginx configs — must use API).
- **Let's Encrypt certs issued** for `proxmox.xransum.com` and `nas.xransum.com` via NPM API DNS-01/Cloudflare challenge.
- **Cutover** — router port 443 switched to edge-vm (192.168.1.173). DNS grey-cloud A records updated: `docker`, `proxmox`, `nas` all point to edge-vm Tailscale IP (100.106.60.22).
- **media-vm proxy profile stopped** — nginx-proxy-manager, cloudflared, cloudflare-ddns containers removed. `docker-compose.yml` stripped of proxy profile services and volumes.
- **All 11 services verified** HTTP 200/401 post-cutover.

**Gotchas:**
- NPM direct SQLite edits do not regenerate nginx configs. Always use the REST API to update proxy hosts.
- NPM's compose project is `edge-stack` so volume names are prefixed `edge-stack_nginx-pm-*`, not `nginx-pm-*`.
- NPM cert API (`POST /api/nginx/certificates`) rejects `letsencrypt_agree`/`letsencrypt_email` in meta — only `dns_challenge`, `dns_provider`, `dns_provider_credentials`, `propagation_seconds` are valid meta fields.
- The `cf` CLI `--body` flag is broken in PowerShell (rejects valid JSON strings). Workaround: use `Invoke-RestMethod` directly with the OAuth token from `~/.config/cloudflare/config/default.json`.
- Proxmox Ubuntu 24.04 VM installer hangs on "installing missing packages" with 1GB RAM — Ubuntu Server requires 1.5GB minimum for ISO install. Used 2GB for install, can drop to 1.5GB post-install.

**Worker 7 status: ✅ completed.**

---

## 2026-10-21 — proxmox.xransum.com + nas.xransum.com moved to Tailscale-only

**Context:** Both `proxmox.xransum.com` and `nas.xransum.com` were previously routed through the Cloudflare tunnel (orange-cloud CNAME → cloudflared → NPM → backend). This is unnecessary for admin UIs that are only accessed by the homelab owner over Tailscale — the same pattern already used for `docker.xransum.com` (Portainer). Moving them off the tunnel removes NPM from the path and lets the browser talk directly to each service's own HTTPS UI.

**Done:**

- **Cloudflare Tunnel ingress rules removed** — pushed updated config to tunnel `8cd27584-61f9-4d09-bf14-09d5573ad366` (version 8) with `proxmox.xransum.com` and `nas.xransum.com` entries deleted. Remaining rules: overseerr, sonarr, radarr, prowlarr, tautulli, rdt, calibre, npm, pelican, gameservers-vm + `http_status:404` catch-all.
- **Cloudflare DNS records swapped** — deleted orange-cloud CNAMEs (both pointed at `8cd27584-...cfargotunnel.com`), created grey-cloud A records:
  - `proxmox.xransum.com` → `100.77.224.51` (Proxmox Tailscale IP)
  - `nas.xransum.com` → `100.67.119.85` (TrueNAS Tailscale IP)
- **NPM proxy hosts deleted** — soft-deleted rows 10 and 11 in NPM's SQLite database (`is_deleted=1`), copied DB back into container, restarted NPM. NPM no longer serves these hostnames.
- **DNS verified** — `nslookup proxmox.xransum.com` returns `100.77.224.51`; `nslookup nas.xransum.com` returns `100.67.119.85`. No Cloudflare proxy in path.

**Access pattern going forward:**

- `https://proxmox.xransum.com:8006` — Tailscale required, browser hits Proxmox directly (self-signed cert, browser warning expected unless cert pinned)
- `http://nas.xransum.com` — Tailscale required, browser hits TrueNAS UI directly

**Why not NPM+SSL for these:** Both services have their own HTTPS and the only users are the homelab owner on Tailscale. Adding NPM+cert management adds complexity with no benefit. Consistent with the `docker.xransum.com` precedent.

**Gotcha — `cf` CLI `--body` flag broken in PowerShell:** The `cf` CLI's `--body` flag rejects JSON passed as a PowerShell string (even valid compact JSON). Workaround: used `Invoke-RestMethod` directly against the Cloudflare API (`PUT /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations`) with the OAuth token from `~/.config/cloudflare/config/default.json`. Similarly, `cf dns records create` has no individual flags — also used the REST API directly for DNS record creation.

---

## 2026-10-20 — Worker 6: Overseerr → Seerr migration

**Context:** Overseerr deprecated upstream; migrated to Seerr (the official successor) on media-vm. Service continued operating at `https://overseerr.xransum.com` — no DNS, NPM, or CF tunnel changes needed.

**Done:**

- Config volume backed up to `/tmp/overseerr-backup.tar.gz` (1.2 MB) before any changes.
- Permissions on the config volume fixed to UID/GID 1000 (`node` user) — required because Seerr dropped the linuxserver wrapper and runs as a non-root user.
- `compose/docker-compose.yml` updated:
  - Image: `lscr.io/linuxserver/overseerr:latest` → `ghcr.io/seerr-team/seerr:latest`
  - Added `init: true` (Seerr image has no init process)
  - Removed `PUID`/`PGID`/`TZ` environment block (not used by Seerr)
  - Volume mount: `overseerr-config:/config` → `overseerr-config:/app/config`
- Seerr pulled and started; auto-migration ran on first boot and logged success.
- API confirmed: `initialized: true`, `applicationTitle: "Seerr"`.
- User confirmed UI loads and data is intact at `https://overseerr.xransum.com`.

**Decisions shifted vs worker doc:**

- Correct image is `ghcr.io/seerr-team/seerr:latest` (not `seerr/seerr:latest` — that tag doesn't exist on Docker Hub; Seerr ships only via GHCR).
- `init: true` is required (not mentioned in worker doc).
- Volume mount path is `/app/config` (not `/config` as linuxserver used).
- Permissions chown step required before starting — omitted from worker doc.
- Config migration was fully automatic; no manual file moves needed.
- `git pull` on media-vm needed `git reset --hard origin/main` due to 12 diverged local commits not present on remote.

**Worker 6 status: ✅ completed.**

---

## 2026-10-19 — Worker 4: devbox-template (VM 102)

### What was done

Created `devbox-template` (VM 102) on Proxmox as a golden snapshot template
for on-demand developer VMs. Full dev environment replicated from `debian-devbox`.

**Key decisions:**
- Replaced pyenv + nvm + rvm with `mise` (single version manager). `rustup` kept
  separately. See `docs/workers/worker-4-devboxes.md` for full rationale.
- Python global pinned to 3.11.9 (all 7 pyenv versions from debian-devbox replicated).
- DHCP not static — stable addressing via Tailscale per clone after `tailscale up`.
- Proxmox snapshot + convert to template are mutually exclusive; snapshot deleted,
  template itself is the golden state.
- opencode installed via `npm install -g opencode-ai` (official install.sh returned 404).
- 64GB disk (upgraded from 32GB spec for Python builds + Docker headroom).
- Dotfiles copied from debian-devbox; opencode config from `xransum/opencode-config` repo.

**Post-clone checklist:** `tailscale up`, `gh auth login`, add SSH config entry,
optionally `aws configure`. See Completion Notes in worker doc for full detail.

---

## 2026-10-19 — Worker 3: gameservers-vm + Pelican Panel + Wings

### What was done

- Created `gameservers-vm` (VM ID 101) on Proxmox: Ubuntu 24.04, 12 vCPU,
  32GB RAM, 512GB disk (q35/UEFI), VirtIO NIC on vmbr0, static IP 192.168.1.170
- Installed: Tailscale (100.117.244.86), Docker, qemu-guest-agent,
  passwordless sudo for ransum, homelab pubkey, Wings binary v1.0.0-rc.1
- Added `gameservers-vm` and `gameservers-vm-ts` to Windows SSH config
- Deployed Pelican Panel via Docker Compose (ghcr.io/pelican/panel:latest,
  SQLite, BEHIND_PROXY=true); panel accessible at https://pelican.xransum.com
- Wings runs as native systemd binary (not Docker); config at /etc/pelican/config.yml
- Wings WebSocket routing: CF Tunnel → NPM (media-vm) → Wings:8080 (plain HTTP).
  Direct LAN DNS approach failed due to modern browser Private Network Access
  protection blocking public domains resolving to RFC-1918 IPs. CF tunnel is
  the correct architecture for this.
- Let's Encrypt cert for gameservers-vm.xransum.com via certbot DNS-01/Cloudflare
  (used for Wings SSL initially, then abandoned in favour of NPM termination)
- NFS backup target: created media/gameservers dataset on TrueNAS, exported,
  mounted at /mnt/gameservers on gameservers-vm. Note: doc said /mnt/dev/gameservers
  but dev pool doesn't exist — corrected to media/gameservers.
- Pelican node registered: FQDN gameservers-vm.xransum.com, port 443,
  HTTPS with reverse proxy, Wings listening on 8080 plain HTTP behind NPM
- DNS: pelican.xransum.com CNAME → tunnel, gameservers-vm.xransum.com CNAME → tunnel
  both orange-cloud through CF tunnel → NPM → respective backends
- Added mc1/mc2/mc3/valheim/satisfactory/factorio SRV + A records;
  cloudflare-ddns updated to manage mc3.xransum.com
- Port forwards on router: 25565/25566/25567 TCP → 192.168.1.170

### Game servers migrated from serverhub (/home/minecraft/)

All 7 servers rsync'd from minecraft@serverhub, ownership set to pelican:pelican,
auto-restart disabled in each server_start.sh:

| Pelican Name | Dir | Egg | Java | Pelican UUID |
|---|---|---|---|---|
| All The Mods 10 (v7.1) | atm10-71 | NeoForge 21.1.234 | 21 | 83c95aef-... |
| All The Mods 10 (v4.5) | atm10 | NeoForge 21.1.191 | 21 | a4595ff6-... |
| All The Mods 9 (v0.3.2) | atm9 | Forge 1.20.1-47.3.7 | 17 | 5f275f4a-... |
| All The Mods 9 Skyblock (v1.1.1) | atm9-sb | Forge 1.20.1-47.2.20 | 17 | 39d94756-... |
| Better Minecraft 4 (v30.5) | bmc4 | Forge 1.20.1-47.3.3 | 17 | 1b557ac4-... |
| Distant Horizons + Iris (v0.4.2) | dh-iris | Fabric | 21 | 1ca0348a-... |
| Sky Factory 4 (v4.2.4) | skyfactory-4 | Forge 1.12.2 | 8 | 276e9e1c-... |

All servers assigned to allocation mc1:25565; reassign in Pelican UI when
running multiple simultaneously.

### Decisions made

- Wings as native systemd binary, not Docker — official Pelican install method;
  no Docker image exists for Wings
- SQLite for Panel DB — sufficient for single-node homelab, no MariaDB/Redis needed
- Pelican compose file simplified from original (dropped MariaDB, Redis, Wings
  container) to match actual Pelican Docker install docs (image was also wrong:
  pelican-dev → pelican)
- Wings WebSocket through CF Tunnel (not direct LAN DNS) — browser Private
  Network Access blocks public domains resolving to LAN IPs in modern Chrome/Firefox
- NFS live game data rejected — live worlds stay on gameservers-vm local disk;
  NAS used for Pelican backups only
- server_start.sh used as startup command for all modpack servers — handles
  NeoForge/Forge self-install on first boot, avoids needing to run installer manually
- dh-iris uses hardcoded java startup command instead of server_start.sh
  (script hardcoded /usr/lib/jvm/msopenjdk-25-amd64/bin/java, doesn't exist in container)
- Auto-restart disabled in all server_start.sh scripts (Pelican manages restarts)
- SRV records for mc1/mc2/mc3 so players connect without specifying port

### State at end of session

- pelican.xransum.com — Panel accessible, Wings online, node green
- ATM10 v7.1 confirmed working: mc1.xransum.com connects in-game
- 6 additional servers created in Panel with data migrated, not yet started
- Steam game servers (serverhub /home/steam/) not migrated — no existing
  worlds worth keeping, will create fresh when needed
- Serverhub decommission still pending (Worker 2 scope)

---

## 2026-10-19 — Worker 2 cleanup complete: rdt-client FinishedAction + download dir purge

**Context:** Second session picking up Worker 2 remaining work. Main decommission was already complete; this session closed out the post-decommission cleanup items.

**Done:**

- **rdt-client FinishedAction set to 2** — was `1` (delete RD torrent, keep local files); changed to `2` (delete torrent + local files after import). Radarr/Sonarr import copies the file, then rdt-client deletes the download dir — no more leftover dirs accumulating. NFS root squash required a workaround: copy DB to `/tmp`, edit as ransum, copy back via `docker run --user 950:950 busybox`.

- **Download dirs cleaned** — deleted all three leftover dirs: `Despicable.Me.2010...`, `Supergirl (2026)...`, `The.Night.Of.S01E01...`. All had already been imported cleanly (confirmed via scan-media logs).

- **Pipeline end-to-end verified** — The Night Of S01E01 and Supergirl (2026) both confirmed imported + ClamAV-scanned clean.

**Decision — NFS hardlink fix dropped:**
TrueNAS only exports per-subdirectory NFS shares. Adding a `/mnt/media` parent export requires sharing a root ZFS dataset (TrueNAS warns against this) and NFS `crossmnt` is not configurable via TrueNAS UI. Even with a parent mount, hardlinks crossing ZFS child dataset boundaries are unreliable. Accepted Option C (copy + delete via FinishedAction=2) as the permanent approach. Temporary 2x disk usage during import is the only downside; acceptable until disk pressure becomes a real concern.

**Worker 2 status: ✅ completed.**

---

## 2026-10-19 — post-decommission service fixes (byparr stack + Overseerr)

**Context:** After serverhub was powered off, several services broke because their configs still pointed at serverhub-era addresses (`localhost`, RAID paths, old hostnames). Fixed everything needed for the pipeline to function end-to-end.

**Fixed:**

- **Overseerr request dialog stuck on "Loading..."** — `settings.json` had Radarr/Sonarr hostnames as `localhost` (serverhub), root folders as `/mnt/raid0/media/...` (serverhub RAID), and Tautulli hostname as `mediaserver.kevin-haas.com`. Updated all to Docker service names (`radarr`, `sonarr`, `tautulli`) and NAS paths (`/mnt/media/movies`, `/mnt/media/tv`). Restarted Overseerr. Requests now work.

- **byparr-unwrap broken (TPB/apibay returning 502)** — `docker-compose.yml` had `BYPARR: http://byparr:8191/v1` for the byparr-unwrap service, but the script reads `UNWRAP_UPSTREAM_URL`, not `BYPARR`. Script was falling back to the default `http://127.0.0.1:8192` which doesn't exist inside Docker. Fixed: renamed env var to `UNWRAP_UPSTREAM_URL: http://byparr:8191` (no `/v1` suffix — script appends the request path, so including `/v1` caused double `/v1/v1` → 404).

- **byparr indexer definitions using `127.0.0.1`** — both `1337x-byparr.yml` and `apibay-byparr.yml` had `127.0.0.1:8881/8882` hardcoded (valid for native systemd, broken in Docker). Updated to Docker service names (`byparr-proxy-1337x:8881`, `byparr-proxy-apibay:8882`). Also updated `compose/custom-definitions/` (the bind-mounted live path Prowlarr reads).

- **ThePirateBay (via Byparr) — now working** — 100 results verified after all above fixes.

**Known limitation — 1337x disabled:**
`1337x (via Byparr)` indexer is disabled in Prowlarr. Prowlarr's Cardigann CSS engine cannot extract anchor text when the indexer base URL is HTTP (not HTTPS) — the `title` field selector returns empty for every release. Root cause is likely a Prowlarr/AngleSharp bug with HTTP base URL resolution. Not worth further debugging given 4 other indexers (TPB, Knaben, Torrent Downloads, YTS) provide sufficient coverage. Re-enable only if a specific release is found exclusively on 1337x.

**Pipeline verified end-to-end:**
Overseerr request → Radarr receives → Prowlarr searches → indexers return results. Actual download not fully tested (Supergirl only had CAM rips; Despicable Me request was submitted but pipeline verification was interrupted).

---

## 2026-10-19 — serverhub decommissioned + Overseerr fixed (Worker 2)

**Context:** Ran Worker 2 decommission job. media-vm confirmed as sole production server.

**Completed:**
- Verified all 17 containers on media-vm are up and healthy.
- Confirmed NAS media sizes match serverhub RAID exactly (2.2T movies, 5.7T TV).
- User confirmed router port forwards switched: 32400 TCP and 443 TCP → 192.168.1.169 (media-vm).
- Verified plex.xransum.com responds over HTTPS (401 auth required = healthy).
- All serverhub systemd services stopped and disabled (plexmediaserver, sonarr, radarr, prowlarr, tautulli, rdt-client, rdt-blocklist, overseerr, byparr).
- Serverhub powered off.
- Fixed Overseerr `settings.json`: Radarr/Sonarr hostnames updated from `localhost` to Docker service names (`radarr`, `sonarr`), root folders updated from serverhub RAID paths to NAS paths (`/mnt/media/movies`, `/mnt/media/tv`), externalUrls updated to `radarr.xransum.com`/`sonarr.xransum.com`, Tautulli hostname updated from `mediaserver.kevin-haas.com` to `tautulli`. Container restarted and connections verified.

**Root cause of Overseerr "Loading..." bug:**
Overseerr's `settings.json` still had serverhub-era config (`hostname: localhost`, old RAID paths). When serverhub was powered off, Overseerr couldn't reach Radarr/Sonarr and the request dialog dropdowns timed out showing "Loading...". Fixed by pointing at Docker service names on the internal `server-stack_default` network.

**Deferred:**
- Media file deletion from serverhub RAID (`/mnt/raid0/media/`) — explicitly deferred to a separate worker job. All data intact on serverhub; NAS is source of truth.
- Game server data (`/home/minecraft/`, `/home/steam/`) untouched — pending Worker 3 migration to gameservers-vm.

---

## 2026-10-19 — media-vm fully operational; migration complete; worker system established

**Context:** Completed full migration from serverhub to media-vm. All services
running in Docker Compose. Established a worker-based job system for remaining tasks.

**Completed:**
- Full Plex migration: DB, metadata, watch history, user profiles all transferred.
  Libraries updated to new NAS paths (/mnt/media/movies, /mnt/media/tv, /mnt/media/audiobooks).
- Sonarr/Radarr/Prowlarr migrated with all quality profiles, custom formats, indexers.
- rdt-client migrated with all settings from serverhub DB (NAS DB at /mnt/media/downloads/rdtclient.db).
  appsettings.json written to named volume to point rdt-client at NAS DB.
- All Plex home users (11 managed accounts) re-granted library access.
- CF tunnel configured: all services routed through cloudflared → NPM → containers.
- cloudflare-ddns container deployed (favonia/cloudflare-ddns) keeping grey-cloud
  records updated: plex, mc1, mc2, valheim, satisfactory, factorio.
- plex.xransum.com: grey cloud direct A record, router port forwards 443+32400 → media-vm,
  NPM SSL with Let's Encrypt cert (expires 2027-01-17). Green padlock.
- Tailscale deployed on media-vm, proxmox, truenas. SSH aliases: *-ts.
- DNS: proxmox.xransum.com → Tailscale, nas.xransum.com → Tailscale.
- All services behind CF tunnel (no direct exposure except plex.xransum.com).
- Worker job system created in docs/workers/ for remaining tasks.
- AGENTS.md updated: deployment status corrected, cloudflare-ddns decision resolved.
- rdt-blocklist and queue-cleaner both running with DRY_RUN=false.
- Byparr cloudflare-bypass stack running (all profiles active).
- Calibre-Web: books imported, inotify watcher auto-imports new files.
- ClamAV + rkhunter run on serverhub after miner incident (no persistence found).
- Plex updated to v1.43.4 on serverhub after XMRig miner found (exploited via port 32400).

**Remaining (see docs/workers/):**
- Worker 1: PCIe GPU passthrough (RTX 2070 still in serverhub)
- Worker 2: Serverhub decommission (independent)
- Worker 3: gameservers-vm + Pelican Panel (independent)
- Worker 4: devbox-template VM (independent)

**Architecture decisions:**
- Plex NOT behind CF tunnel (ToS prohibits video streaming at scale). Uses direct
  port forward + NPM SSL instead.
- All other services behind CF tunnel via NPM.
- cloudflare-ddns replaces deprecated dns-updater for grey-cloud record management.
- Single homelab SSH key (~/.ssh/homelab ed25519) for all homelab hosts.

---

## 2026-10-18 — media-vm deployment started; SSH hardening; compose updates

**Context:** Proxmox hardware arrived and media-vm (Ubuntu 24.04, 192.168.1.169)
was provisioned. Started active deployment of the Docker Compose stack.
serverhub is still running as production while media-vm is being set up.

**SSH:**
- Generated `homelab` ed25519 key (`~/.ssh/homelab`) as the single key for
  all homelab hosts (proxmox, media-vm, truenas, future VMs).
- Deployed to: `root@192.168.1.168` (proxmox), `ransum@192.168.1.169` (media-vm),
  `truenas_admin@192.168.1.164` (truenas, via web UI).
- Hardened proxmox: `PermitRootLogin prohibit-password`, `PasswordAuthentication no`.
- Hardened media-vm: `PasswordAuthentication no`.
- TrueNAS already blocks password SSH — key-only by default.
- SSH config aliases: `proxmox`, `media-vm`, `truenas` all use `homelab` key.

**NFS discovery:**
- TrueNAS exports 6 separate NFS shares (not one `/mnt/media`):
  `movies`, `tv`, `music`, `audiobooks`, `books`, `downloads` — all under
  `/mnt/media/`, all scoped to `192.168.1.0/24`, all rw.
- Updated compose fstab comment and all service volume mounts to match the
  per-directory pattern instead of the previous single `/mnt/media` mount.

**Compose fixes:**
- `docker-compose.yml` header updated from Debian 12 → Ubuntu 24.04, status
  changed from UNTESTED to ACTIVE.
- All NFS volume mounts updated to per-subdir pattern (each service only mounts
  what it needs).
- Added `calibre-web` service to `media` profile (port 8083,
  `DOCKER_MODS=linuxserver/mods:universal-calibre`, `/mnt/media/books:/books`).
- Added `calibre-web-config` named volume.
- Fixed `rdt-blocklist` Dockerfile: `USER nobody` → `USER 1000` (nobody cannot
  read the rdt-client DB owned by UID 1000 — would silently fail at runtime).
- Fixed `compose/.env.example`: `PELICAN_DB_PASSWORD=changeme` → empty string.
- Updated `compose/README.md`: fixed Debian 12 → Ubuntu 24.04, added
  calibre-web to profiles/service URL tables, marked reverse proxy (NPM) and
  S3 (MinIO on TrueNAS) open decisions as resolved.

**Security note (serverhub):**
- Discovered XMRig cryptominer running as `plex` user on serverhub, dropped
  via Plex RCE exploit (port 32400 was open to internet on Plex v1.42.2).
- Killed miner, updated Plex to v1.43.4, locked port 32400 to LAN only.
- Friends access Plex via app.plex.tv relay (no inbound port needed).
- rkhunter and ClamAV scans run — no persistence found.

**Deferred:**
- PCIe passthrough (RTX 2070) to media-vm — deferred until stack is confirmed
  working. Plex will software transcode until then.
- NFS mounts on media-vm — next step.
- `.env` creation and stack bring-up — next step.

---

## 2026-10-08 -- Game servers move to Pelican; service inventory + deprecations

**Context:** Finalized the game-server subsystem for the Proxmox migration and
captured the authoritative service inventory. The two-VM model is unchanged:
`media-vm` (media stack) + `gameservers-vm` (game servers), plus on-demand dev
boxes. No change to that architecture -- the two fixed VMs have a resource
floor with plenty of headroom.

**Decision 1 -- Pelican for game servers (supersedes Pterodactyl placeholder):**
Game servers are managed by **Pelican** (the actively maintained Beta successor
to Pterodactyl): a **Panel** (web UI + API) plus a **Wings** node daemon that
runs each game as its own Docker container from an "egg". Each game = its own
Pelican server, started on demand. Hard constraint: **Wings requires a full KVM
VM, not an LXC** -- `gameservers-vm` already is a KVM VM, so this holds; noted
in `docs/proxmox-compute.md` so it is not "optimized" into an LXC later.
Replaced the per-game compose services in
`compose/docker-compose.gameservers.yml` with a Panel + MariaDB + Redis + Wings
stack; dropped the now-obsolete per-game env vars from `compose/.env.example`
and rewrote the `compose/README.md` game section. DNS admin host renamed
`pterodactyl.xransum.com` -> `pelican.xransum.com`.

**Decision 2 -- game storage = local disk + Pelican backups (supersedes live
NFS worlds):** Live worlds run on the `gameservers-vm` local disk (Wings data
dir) to avoid SQLite/world corruption and latency over NFS. Durability comes
from **Pelican scheduled backups** pushed to the NAS `dev/gameservers` dataset,
now reframed in `docs/storage.md` as a **backup target** (not a live mount),
with ZFS snapshots (7d / 4w) giving a second restore path.

**Decision 3 -- service inventory + deprecations:** Captured the authoritative
roster in `docs/proxmox-compute.md` (new Service inventory section) with status
flags. Deprecated (do not migrate, retire): `dns-updater` (superseded by CF
Tunnel), `FlareSolverr` (replaced by Byparr), `byparr-proxy-1337x` (upstream
solve fails). Target game roster: Pelican Panel/Wings, Minecraft Java (PaperMC),
Minecraft Bedrock, Valheim, Palworld, Counter-Strike 2.

**Migration (inventory-agnostic):** `docs/migration.md` gained Phase 5.5
(game-server migration) written to **enumerate live at cutover and copy whole
home trees wholesale**, so servers added before the final build are captured
automatically -- no hardcoded inventory as source of truth. Flow: stage homes
to the NAS, stand up Pelican, create servers from eggs, import worlds via
SFTP/file manager, verify each loads with existing progress. Phase 8 guarded so
game-server home dirs (`/home/minecraft`, `/home/steam`, etc.) are not deleted
until Phase 5.5.5 is verified.

---

## 2026-08-08 — rdt-blocklist service added; path typos fixed

**Context:** Observed live on serverhub that when Real-Debrid rejects a
torrent hash as infringing, rdt-client records the error in its database but
reports the download as healthy to Sonarr/Radarr (`status=warning,
trackedDownloadStatus=ok`). The item sits in the queue indefinitely; no
alternative release is grabbed. Manually blocklisting and re-searching was
required each time.

**Root cause:** rdt-client does not propagate a failure signal back to the
*arr download client API when the provider error occurs. Sonarr/Radarr never
see a failed state. queue-cleaner catches a related but later-stage failure
(zero-size warning items); the infringing case sometimes never reaches that
stage.

**Decision:** Add `rdt-blocklist` — a new long-running sidecar that polls the
rdt-client SQLite database directly every 2 minutes, matches stuck rows against
the Sonarr/Radarr queue by title, blocklists the matched queue item (with
`removeFromClient=true, blocklist=true`), triggers a fresh episode/movie
search, and deletes the stuck database row. Both queue-cleaner and
rdt-blocklist can run simultaneously — they target different failure stages.

Observed catching and auto-resolving infringing releases for Widows Bay
S01E01 within seconds of the error appearing in the rdt-client database.

**Files added:**
- `scripts/rdt-blocklist.py` — stdlib-only polling daemon
- `scripts/rdt-blocklist.env.example`
- `services/rdt-blocklist.service` — persistent systemd unit (not a timer)
- `docker/rdt-blocklist/Dockerfile`
- `docker/rdt-blocklist/example.env`
- `docs/rdt-blocklist.md`
- compose: `rdt-blocklist` service added to `indexers` profile

**Also fixed:** `/mnt/raid/media/` corrected to `/mnt/raid0/media/` across
`docs/radarr.md`, `docs/sonarr.md`, `docs/permissions.md`, `docs/clamav.md`,
and `docs/rdt-client.md`. The live server mount point was always `raid0` but
the docs had a stale path.

---

## 2026-06-02 — Hardware finalized, open decisions resolved

**Context:** Extended planning session covering NAS hardware ordering,
compute platform decision, network simplification, and domain strategy.

**Decisions:**

- NAS hardware ordered. Full BOM in `docs/homelab-hardware.md`. Total:
  $1,193.37. Core change from earlier drafts: dropped Supermicro X10SRi-F +
  Xeon E5 (ECC, IPMI overkill for a home NAS) in favor of ASRock B550M Pro4 +
  Ryzen 5 5600G. Saves ~$600 for identical NAS performance.
- Compute platform: AM5 (Ryzen 9 7950X3D + ASUS ProArt X870E). Waiting on
  DDR5 64GB to drop from $829 to ~$450-500 before ordering. Expected Q4 2026.
  AM4/5950X rejected: would be a temporary build we'd want to replace.
- Network simplified: dumb TP-Link TL-SX1008 10GbE switch + retain FIOS
  gateway. MikroTik router rejected — adds complexity that Tailscale (remote
  access) and Cloudflare Tunnel (public exposure) already handle. No managed
  switch needed for a 2-server homelab.
- Domain: `xransum.com` for all homelab services. `kevin-haas.com` stays on
  GitHub Pages for the blog. No collision.
- Reverse proxy: Nginx Proxy Manager selected. Resolves the open decision.
  Docker image: `jc21/nginx-proxy-manager`. Added to
  `compose/docker-compose.yml` under the `proxy` profile.
- S3: MinIO on TrueNAS Scale as a native app (not Docker). Resolves the open
  decision.
- DNS/exposure strategy finalized:
  - HTTP services: Cloudflare Tunnel (orange cloud, IP hidden)
  - Game server TCP/UDP ports: grey cloud A records on `xransum.com`, direct
    FIOS port forwards, `dns-updater` keeps IP current
  - Tailscale for all internal access (devboxes, admin UIs)

**Migration additions:**

- Media drive on `serverhub` is 95% full (448GB free). Migration is
  time-sensitive.
- Correct media path: `/mnt/raid0/media/` (not `/mnt/raid/`). Subdirs:
  `Audio`, `Downloads`, `Ebooks`, `Videos`.
- Plex database correct path:
  `Plug-in Support/Databases/` (not `Databases/`). System `sqlite3` cannot
  check integrity (`icu_root` collation missing). Plex verifies internally on
  startup.
- Plex has 15+ active user profiles. Data migration must complete before the
  new container starts. ~12GB total.
- Overseerr is a snap install on `serverhub` (non-standard config path at
  `/var/snap/overseerr/current/`).
- Services not migrating: Apache2 (unused), FlareSolverr (replaced by Byparr),
  Rust server (inactive), Minecraft (template only, no instances).

---

## 2026-05-25 — Homelab architecture planning

### Context

Extended planning session to future-proof the repo for a Proxmox/Docker/NAS
migration. Current state: everything runs as native systemd on `serverhub`
(Debian 12, 192.168.1.166). Migration target: two-server homelab (TrueNAS
NAS + Proxmox compute), services running in Docker Compose on Debian VMs.

### Decisions made

**Architecture:**
- NAS: TrueNAS Scale, ZFS mirror vdevs, NFS exports to compute VMs
- Compute: Proxmox host with two primary VMs:
  - `media-vm` — Docker host for the full media stack + byparr-proxy +
    reverse proxy + S3 service. NVIDIA 2070 PCIe passthrough for Plex NVENC.
  - `gameservers-vm` — Docker host for game servers (Minecraft, Valheim,
    Palworld, Source engine, etc.). On-demand, spin up per game, archive
    worlds to NAS when done.
  - `devbox-*` — Proxmox linked clones from a base template. SSH over
    Tailscale from phone (NeoServer) and laptop, both on and off network.

**Storage (ZFS topology):**
- Mirror vdevs, not RAIDZ. Reason: RAIDZ vdev shape is fixed at creation.
  Mirrors allow adding pairs of drives indefinitely without rebuilding.
  "Add drives in pairs to not break the bank" maps directly to `zpool add
  media mirror <sda> <sdb>` — pool grows transparently, `/mnt/media` mount
  point never changes, NFS exports and Docker volume mounts need zero
  reconfiguration. 8× 12TB = 4 mirror vdevs = ~48TB usable, expandable.
- Single `/mnt/media` NFS share covering `downloads/` and `media/` so
  Sonarr/Radarr can hardlink on import (same filesystem = instant, no copy).

**External access:**
- Tailscale for all personal/admin access (SSH to devboxes, all service UIs,
  NAS admin, Proxmox). Install on phone + laptop, works anywhere, no port
  forwarding, no public exposure.
- Cloudflare Tunnel for Overseerr only (public-facing, friends/family request
  media without needing Tailscale). Everything else stays off the public internet.
- `dns-updater` service deprecated once CF Tunnel is live.
- Reverse proxy (nginx/Caddy/Nginx PM — decision deferred) for internal subdomain
  routing + TLS via Cloudflare DNS-01 wildcard cert.

**Real-debrid client:** rdtclient (already running on serverhub, works well).

**Game servers:** Pterodactyl Panel as management UI, per-game Docker containers,
world saves on NAS NFS mount so they survive container rebuilds. Archive =
ZFS snapshot the NAS dataset.

**Repo structure additions:**
- `AGENTS.md` — conventions, parity rules, doc philosophy (this session)
- `docker/` — Dockerfiles for custom services
- `compose/` — Docker Compose stack (untested until lab hardware)
- `docs/storage.md` — ZFS pool layout, NAS setup, expansion guide
- `docs/proxmox-compute.md` — VM layout, GPU passthrough, devbox workflow
- `docs/homelab-hardware.md` — hardware BOM, shopping list, build order
- `docs/migration.md` — step-by-step cutover from serverhub to lab

**Deferred decisions (flagged as open in AGENTS.md and respective docs):**
- Reverse proxy pick (Caddy / Nginx PM / Traefik / nginx)
- S3 service pick (Garage / MinIO / Nextcloud)
- Compute server CPU/RAM spec (affects VM sizing)
- Per-game RAM/CPU limits in compose file

### Why Tailscale over Mullvad/WireGuard-DIY

Mullvad is an outbound privacy VPN — it routes *your* traffic through their
servers. It doesn't help you reach your home servers from overseas; it makes
it harder. Tailscale is a mesh VPN that connects your devices directly to each
other. Your phone in Tokyo reaches your devbox at home over WireGuard P2P
with no third-party relay. Tailscale's coordination server is hosted by them
but never sees your traffic (WireGuard is E2E encrypted). Free personal tier
covers well over 100 devices. No port forwarding required.

The MikroTik router (in the hardware plan) has WireGuard built into RouterOS
and could serve as a self-hosted coordination server alternative (Headscale
equivalent), but the operational overhead isn't worth it for personal use.
Tailscale stays as the permanent answer.

---

## 2026-05-25 — Byparr + apibay Turnstile bypass

### Context

Goal: get ThePirateBay (apibay JSON API) working in Prowlarr without
Cloudflare Turnstile blocking. serverhub was already running Byparr for
1337x (with byparr-proxy fronting it), but apibay had additional complications.

### Key discoveries

**Prowlarr cookie-replay wall:**
Prowlarr's FlareSolverr/Byparr integration harvests cookies from the bypass
solve, then discards Byparr's response body and replays the request directly
with those cookies. Cloudflare Turnstile issues no `cf_clearance` cookie —
the replay always fails regardless of whether Byparr solved the challenge.
This is fundamental to how Prowlarr's indexer proxy works, not a config bug.
byparr-proxy short-circuits this entirely: Prowlarr's "Base URL" points at
`http://127.0.0.1:8881/` (local), so every request goes through byparr-proxy
→ Byparr → site. Prowlarr never enters the cookie-replay path because it
never sees a Cloudflare response.

**Byparr wraps non-HTML in Firefox plaintext-viewer HTML:**
Byparr serves all responses through a real Firefox browser. When the upstream
response is non-HTML (JSON, RSS, XML), Firefox wraps it in its own
plaintext-viewer HTML shell (`<html><head>...<pre>JSON</pre>...</html>`).
The byparr-unwrap sidecar (port 8193) strips this wrapper and returns the
raw body. Required for any JSON/RSS indexer (apibay, RSS-only feeds).
HTML indexers like 1337x do not need it.

**Cardigann URL-encoder bug:**
Prowlarr's Cardigann engine runs every `{{ .Config.var }}` value substituted
into `search.paths[*].path` through `WebUtility.UrlEncode`. This mangles
`127.0.0.1:8882` into `127.0.0.1%3A8882`, producing an invalid URI that
.NET's parser rejects with "Invalid URI: The hostname could not be parsed."
Fix: hardcode the proxy URL as literal text in the path template (literal
text is not encoded, only substituted values are). The `apiurl` setting was
downgraded to `type: info` with an explanation. See
`CardigannRequestGenerator.cs` → `ApplyGoTemplateText(..., WebUtility.UrlEncode)`.

**1337x Byparr solve currently fails:**
Upstream Byparr/Camoufox times out on 1337x (~64s). This is an upstream
issue, not our config. The byparr-proxy@1337x infrastructure is correct and
will work once upstream fixes it. apibay solves in ~5s.

### Decisions made

- byparr-proxy@apibay → byparr-unwrap (8193) → byparr (8192) chain
- byparr-proxy@1337x → byparr (8192) directly (HTML, no unwrap needed)
- `apibay-byparr.yml` hardcodes `http://127.0.0.1:8882/` in `search.paths`
- `apiurl` setting removed, replaced with `type: info` explaining the limitation
- `byparr-unwrap.service` re-enabled (had been disabled during earlier cleanup)
- `flaresolverr.service` stopped and disabled, kept in repo with deprecation
  banner for reference

### State at end of session

- apibay Test passes in Prowlarr (~5s), full chain working end-to-end
- 1337x infrastructure correct, blocked by upstream Byparr issue
- All commits pushed to origin/main (see git log for specifics)
