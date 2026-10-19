# Worker 2 — Decommission Serverhub

## Status
⏳ pending

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

(fill in when done)
