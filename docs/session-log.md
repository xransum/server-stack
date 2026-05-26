# Session Log

Running log of significant decisions, discoveries, and session outcomes.
Purpose: future-you (or any agent picking up this repo) should be able to
skim this file and immediately understand what was done, why, and what was
deferred — without reading commit diffs or reverse-engineering the code.

Format: newest entry at the top.

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
- Reverse proxy (nginx/Caddy/NPM — decision deferred) for internal subdomain
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
- Reverse proxy pick (Caddy / NPM / Traefik / nginx)
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
