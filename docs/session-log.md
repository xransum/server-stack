# Session Log

Running log of significant decisions, discoveries, and session outcomes.
Purpose: future-you (or any agent picking up this repo) should be able to
skim this file and immediately understand what was done, why, and what was
deferred — without reading commit diffs or reverse-engineering the code.

Format: newest entry at the top.

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
