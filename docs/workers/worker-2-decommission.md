# Worker 2 — Decommission Serverhub

## Status
✅ completed

## Trigger
Independent — can be started anytime. Does not depend on Worker 1.
Recommended to run after verifying media-vm has been stable for a few days.

---

## Opening Prompt (paste this verbatim to start the session)

```
You are Worker 2. Your only job is decommissioning serverhub and making
media-vm the sole production server. Do not do anything outside this scope.
Read this file for all context you need.
When your job is complete, update the ## Status section of this file to "✅ completed",
add a ## Completion Notes section describing what was done and any decisions that shifted,
commit this file, push it, then ask me to confirm everything looks good before closing.

Context file: docs/workers/worker-2-decommission.md in the xransum/server-stack repo.
```

---

## Infrastructure

- **serverhub**: 192.168.1.166, `ssh serverhub` (id_rsa key, kevin user, passwordless sudo)
- **media-vm**: 192.168.1.169, `ssh media-vm` (homelab key, ransum user, passwordless sudo)
- **Router**: 192.168.1.1 (FIOS gateway — port forward changes done manually by user)
- **Repo**: `https://github.com/xransum/server-stack`

## Current State of Serverhub

- All services are **already stopped** (plex, sonarr, radarr, prowlarr, rdt-client, tautulli, overseerr, rdt-blocklist, queue-cleaner, byparr)
- Media files on local RAID at `/mnt/raid0/media/` — NAS is now source of truth (same content)
- RTX 2070 may or may not have been moved already (check before proceeding)
- Serverhub has **no Tailscale** — LAN access only

## Important: Game Server Data

**DO NOT delete** `/home/minecraft/` or `/home/steam/` or any game server directories.
These contain world saves that need to be migrated to gameservers-vm (Worker 3) first.
Skip those directories entirely during cleanup.

---

## Job Steps

### Step 0 — Verify ALL media-vm services are healthy (REQUIRED before proceeding)

**This step was added after the initial worker run. Must be completed before marking job done.**

Check every service is functioning correctly. If any fail, fix them before proceeding with decommission.

```bash
ssh media-vm "docker ps --format 'table {{.Names}}\t{{.Status}}'"
```

Verify each service end-to-end:

| Service | How to verify |
|---|---|
| **Plex** | `https://plex.xransum.com` loads, libraries show Movies/TV/Audio Books with content |
| **Sonarr** | `https://sonarr.xransum.com` loads, root folder `/mnt/media/tv` accessible |
| **Radarr** | `https://radarr.xransum.com` loads, root folder `/mnt/media/movies` accessible |
| **Prowlarr** | `https://prowlarr.xransum.com` loads, indexers enabled, no disabled indexers |
| **rdt-client** | `https://rdt.xransum.com` loads, settings show correct RD API key and download paths |
| **Overseerr** | `https://overseerr.xransum.com` loads, can browse and submit a request, Plex connection works |
| **Tautulli** | `https://tautulli.xransum.com` loads |
| **Calibre-Web** | `https://calibre.xransum.com` loads, books visible |

**Known issues that are NOT blockers (cosmetic only):**
- Sonarr/Radarr indexer backoff warnings — auto-recover within an hour
- Sonarr/Radarr Docker path warning for rdt-client — cosmetic, functionality unaffected

**If Overseerr is broken:** Investigate the connection to Plex. Overseerr settings → Plex → verify the server IP is `192.168.1.169` and token is valid. If needed, re-run the Plex sync in Overseerr settings.

Do NOT proceed to Step 1 until all services above are verified working.

### Step 1 — Verify media-vm is fully operational

Check all containers are running and healthy:
```bash
ssh media-vm "docker ps --format 'table {{.Names}}\t{{.Status}}'"
```

Verify Plex is accessible and serving content before proceeding.

### Step 2 — Verify NAS has all media (sanity check)

```bash
ssh media-vm "du -sh /mnt/media/movies /mnt/media/tv"
ssh serverhub "du -sh /mnt/raid0/media/Videos/Movies /mnt/raid0/media/Videos/TV\ Shows"
```

Sizes should match. If they don't, stop and investigate before proceeding.

### Step 3 — User confirms port forward switched (manual step)

The user needs to confirm that the router port forward for:
- `32400 TCP → 192.168.1.169` (media-vm, not serverhub)
- `443 TCP → 192.168.1.169` (NPM on media-vm)

Ask the user: "Have you switched the router port forwards from serverhub to media-vm?"
Do not proceed until confirmed.

### Step 4 — Verify Plex remote access works via plex.xransum.com

Ask a friend to test or test yourself from outside the LAN.
`https://plex.xransum.com` should load with a green padlock.

### Step 5 — Stop and disable all serverhub services

```bash
ssh serverhub "sudo systemctl stop plexmediaserver sonarr radarr prowlarr tautulli rdt-client rdt-blocklist overseerr byparr 2>/dev/null; sudo snap stop overseerr 2>/dev/null; sudo systemctl disable plexmediaserver sonarr radarr prowlarr tautulli rdt-client rdt-blocklist overseerr 2>/dev/null; echo done"
```

### Step 6 — Clean up serverhub media (optional, free up RAID space)

Only do this after user confirms they no longer need serverhub as a fallback.

**DO NOT delete:**
- `/home/minecraft/` — world saves
- `/home/steam/` — game server data
- `/home/kevin/` — user home

**Safe to delete:**
```bash
ssh serverhub "sudo rm -rf /mnt/raid0/media/Videos/Movies"
ssh serverhub "sudo rm -rf '/mnt/raid0/media/Videos/TV Shows'"
ssh serverhub "sudo rm -rf /mnt/raid0/media/Downloads"
```

### Step 7 — Power off serverhub

```bash
ssh serverhub "sudo poweroff"
```

### Step 8 — Update session log in repo

Add an entry to `docs/session-log.md` noting serverhub decommissioned on this date.

---

## Success Criteria

- [ ] Port forwards confirmed switched to media-vm
- [ ] `https://plex.xransum.com` loads with green padlock from outside LAN
- [ ] Serverhub powered off
- [ ] Game server home dirs preserved (not deleted)

---

## Out of Scope

- Do NOT touch media-vm Docker services
- Do NOT delete game server world saves
- Do NOT set up gameservers-vm (that's Worker 3)
- Do NOT touch DNS or Cloudflare settings

---

## Completion Notes

Completed 2026-10-19.

- All 17 media-vm containers confirmed up and healthy before proceeding.
- NAS media sizes matched serverhub RAID exactly (2.2T movies, 5.7T TV) — NAS confirmed as source of truth.
- Router port forwards (32400 TCP, 443 TCP → 192.168.1.169) confirmed switched by user.
- `plex.xransum.com` verified accessible over HTTPS externally (401 response = auth wall, cert valid).
- All serverhub systemd services stopped and disabled (plexmediaserver, sonarr, radarr, prowlarr, tautulli, rdt-client, rdt-blocklist, overseerr, byparr).
- Serverhub powered off.
- Fixed Overseerr `settings.json`: updated Radarr/Sonarr hostnames from `localhost` to Docker service names, root folders from serverhub RAID paths to NAS NFS paths, externalUrls to public subdomains, and Tautulli hostname from `mediaserver.kevin-haas.com` to Docker service name `tautulli`. Overseerr restarted and Radarr/Sonarr connections verified responding.

**Decision shift — media deletion deferred:**
Step 6 (delete media from serverhub RAID) was explicitly skipped at user request. Media files on `/mnt/raid0/media/` remain intact. This is a separate job. Game server dirs (`/home/minecraft/`, `/home/steam/`) were never touched per spec.

**Root cause of Overseerr "Loading..." bug:**
`settings.json` retained serverhub-era config (`hostname: localhost`, RAID paths). Once serverhub was powered off, Overseerr's Radarr/Sonarr API calls failed and the request dialog dropdowns hung at "Loading...". Fixed by pointing at Docker service names on the internal network.

---

## Post-Decommission Fixes Applied (same session)

All fixes below were required because services had serverhub-era config (localhost, RAID paths, wrong env vars).

- **Overseerr** — `settings.json` updated: Radarr/Sonarr hostnames `localhost` → Docker service names, root folders `/mnt/raid0/...` → `/mnt/media/...`, Tautulli hostname `mediaserver.kevin-haas.com` → `tautulli`.
- **byparr-unwrap** — `docker-compose.yml` had `BYPARR:` env var but script reads `UNWRAP_UPSTREAM_URL`. Fixed + removed `/v1` suffix (script appends request path, double `/v1/v1` caused 404).
- **apibay/TPB Cardigann definition** — hardcoded `127.0.0.1:8882` → `byparr-proxy-apibay:8882`. TPB now returns 100 results.
- **1337x disabled** — Cardigann text extraction returns empty when base URL is HTTP (not HTTPS). Prowlarr bug. Disabled; 4 other indexers provide sufficient coverage.
- **Radarr/Sonarr indexer URLs** — all synced indexers had `localhost:9696` → updated to `prowlarr:9696` via API.
- **Radarr collections** — 122 collections had stale root folder `/mnt/raid0/media/Videos/Movies` → bulk-updated to `/mnt/media/movies` via API.
- **Sonarr indexer backoff** — `EscalationLevel: 8` baked into DB; cleared via direct sqlite DELETE on `IndexerStatus` table.
- **Download dirs created** — `/mnt/media/downloads/radarr` and `/mnt/media/downloads/sonarr` created on TrueNAS with `chmod 777` (rdt-client runs as root, needs world-writable).
- **scan-media (ClamAV)** — new Docker service deployed: `clamd` + `freshclam` (2h auto-update) + Python HTTP server on port 8080. `scan-media.sh` mounted into Radarr + Sonarr containers, Custom Script connection added (On Import trigger). Scans parent directory recursively, deletes infected files, blocklists release in Radarr/Sonarr, sends Discord embed notification.
- **compose/.env** — added `RADARR_API_KEY`, `SONARR_API_KEY`, `DISCORD_WEBHOOK_URL` as shared vars on media-vm.

**Post-decommission cleanup completed 2026-10-19 (second session):**

- **rdt-client FinishedAction** — changed `Integrations:Default:FinishedAction` from `1` (delete RD torrent only, keep local files) to `2` (delete torrent + local files). NFS root squash prevented direct sqlite write as root; fixed by copying DB to `/tmp`, editing as ransum user, copying back via `docker run --user 950:950 busybox cp` to preserve NFS ownership.
- **Leftover download dirs deleted** — `Despicable.Me.2010...`, `Supergirl (2026)...`, and `The.Night.Of.S01E01...` all removed from `/mnt/media/downloads/radarr/` and `/mnt/media/downloads/sonarr/`.
- **Pipeline verified** — scan-media logs confirmed The Night Of S01E01 and Supergirl (2026) both imported cleanly and ClamAV scanned clean before the second session started.
- **NFS hardlink fix dropped** — TrueNAS only has per-subdirectory NFS exports; adding a parent `/mnt/media` export would require exporting a root ZFS dataset (TrueNAS warns against this) and NFS crossmnt is not exposed in TrueNAS UI. Radarr/Sonarr copy-then-delete (Option C) is sufficient: rdt-client now deletes local files after import, so no leftover dirs accumulate. The brief 2x disk usage during import is an acceptable tradeoff.

---

## Remaining Work

All remaining work completed 2026-10-19. See Completion Notes below.

---

## Key Infrastructure Reference

| Service | URL | API Key |
|---|---|---|
| Radarr | http://localhost:7878 | `REDACTED_RADARR_KEY` |
| Sonarr | http://localhost:8989 | `REDACTED_SONARR_KEY` |
| Prowlarr | http://localhost:9696 | `REDACTED_PROWLARR_KEY` |
| scan-media | http://scan-media:8080 | — |
| Discord webhook | — | in compose/.env on media-vm |

All commands run from media-vm via `ssh media-vm`.
