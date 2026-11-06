# Worker 9 -- TorBox Migration (rdt-client replacement)

## Status
⏳ pending

## Trigger
Independent -- anytime. RDT infringing file rejections have become severe enough
to justify migrating to TorBox as the primary debrid provider.

---

## Background

Real-Debrid's infringing file rejections (error code 35) have escalated
dramatically. The rdt-blocklist script was built specifically to fight this
problem, which is itself evidence of how bad it is. Community reports indicate
~90% grab failure rates in mid-2026 for some users. TorBox does not perform
DMCA-level hash blocking.

rdt-client is hardcoded to Real-Debrid via the RDNET SDK -- it cannot be
repointed at TorBox. The replacement is TorBoxarr (MrJoiny/TorBoxarr), a Go
binary that impersonates a qBittorrent API locally and translates calls to
TorBox's REST API. It downloads files to real local disk, works with NFS, and
supports hardlinks -- the correct choice for this setup.

Decypharr (the other popular TorBox client) uses WebDAV FUSE + symlinks.
Symlinks break at NFS boundaries. Do NOT use Decypharr here.

---

## Opening Prompt (paste verbatim to start the session)

```
You are Worker 9. Your only job is migrating from rdt-client (Real-Debrid) to
TorBox via TorBoxarr on media-vm, in three phases. Each phase ends with a
compaction checkpoint. Read this entire file before doing anything.

Repo: https://github.com/xransum/server-stack
Local on media-vm: /opt/media/server-stack
SSH: ssh media-vm (key ~/.ssh/homelab, user ransum, passwordless sudo)
Windows local: ~/Documents/Gits/server-stack

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs.
Use placeholders only. Secrets go in gitignored env files on media-vm only.

When your job is complete (all three phases done), update ## Status to
"completed", add ## Completion Notes, commit this file, push, then ask
for confirmation before closing.

IMPORTANT: This job has three phases. At the end of each phase, stop and
compact the session using the compaction prompt provided in that phase.
Do not proceed to the next phase until instructed after compaction.
```

---

## Infrastructure

- **media-vm**: 192.168.1.169, Ubuntu 24.04, ransum user, passwordless sudo
- **SSH**: `ssh media-vm` (key `~/.ssh/homelab`)
- **Compose**: `/opt/media/server-stack/compose/docker-compose.yml`
- **Active profiles**: `media`, `indexers`, `cloudflare-bypass`
- **NFS mounts on media-vm**: `/mnt/media/downloads`, `/mnt/media/tv`, `/mnt/media/movies`, etc. (all from 192.168.1.164)
- **Current download client**: rdt-client (container: `rdt-client`, port 6500, DB at `/data/db/rdtclient.db` in named volume `server-stack_rdt-client-config`)
- **TorBox API key**: in `~/.env` on Windows as `TORBOX_API_KEY` -- read it on media-vm from the env file or ask the user to provide it securely. NEVER commit it.
- **Sonarr**: http://sonarr:8989 (container), http://localhost:8989 (host)
- **Radarr**: http://radarr:7878 (container), http://localhost:7878 (host)
- **Sonarr/Radarr API keys**: in `/opt/media/server-stack/compose/queue-cleaner.env` on media-vm (gitignored)

---

## TorBoxarr reference

- **Repo**: https://github.com/MrJoiny/TorBoxarr
- **What it is**: Go binary that impersonates qBittorrent Web API v2 + SABnzbd API, translates to TorBox REST API
- **File delivery**: Downloads files to real local disk via HTTP range requests (not symlinks, not WebDAV)
- **Config**: `TORBOXARR_DATA_ROOT` sets where completed files land -- set to `/mnt/media/downloads`
- **Categories**: qBit categories map to subdirectories -- `sonarr` → `/mnt/media/downloads/completed/sonarr/`, `radarr` → `/mnt/media/downloads/completed/radarr/`
- **PUID/PGID**: Must match Sonarr/Radarr (1000/1000) for NFS write permissions
- **Port**: 8085 (default qBit API port in TorBoxarr)
- **No Docker Hub image**: Build from source -- clone repo to `/opt/media/torboxarr` on media-vm
- **API rate limit**: TorBox enforces 60 req/hr -- TorBoxarr handles this internally with a rate limiter
- **Known issue**: TorBox tasks not auto-deleted from cloud after import (PR #8 not merged) -- torbox-blocklist will handle this
- **Known issue**: HTTP .torrent URL not fetched before submission (PR #11) -- check if merged, patch if not
- **Update path**: `cd /opt/media/torboxarr && git pull && docker build ...` -- no hash pinning, track latest

## TorBox API reference

- **Base URL**: https://api.torbox.app/v1/api
- **Auth**: `Authorization: Bearer <TORBOX_API_KEY>`
- **Key endpoints**:
  - `GET /torrents/mylist` -- list all torrents with status
  - `GET /torrents/mylist?id={id}` -- specific torrent status
  - `DELETE /torrents/controltorrent` -- delete a torrent
- **Status values**: `queued`, `downloading`, `cached`/`completed`, `stalled`, `error`
- **Rate limit**: 60 req/hr, HTTP 429 on breach

---

## Phase 1 -- TorBoxarr parallel setup

**Goal**: TorBox running alongside rdt-client. New grabs can go to either.
Verify one complete download end-to-end before proceeding to Phase 2.

### Step 1.1 -- Clone and build TorBoxarr on media-vm

```bash
ssh media-vm "sudo git clone https://github.com/MrJoiny/TorBoxarr /opt/media/torboxarr && sudo chown -R ransum:ransum /opt/media/torboxarr"
```

Review the source before building -- at minimum check:
- How the API key is handled (env var, not hardcoded)
- No unexpected network calls beyond api.torbox.app
- The Dockerfile is a clean multi-stage Go build

Build the image:
```bash
ssh media-vm "cd /opt/media/torboxarr && docker build -t server-stack/torboxarr:local -f deploy/Dockerfile . 2>&1 | tail -10"
```

Record the git commit hash of what was built:
```bash
ssh media-vm "cd /opt/media/torboxarr && git rev-parse HEAD"
```
Document this hash in ## Completion Notes.

### Step 1.2 -- Create torboxarr.env on media-vm

Create `/opt/media/server-stack/compose/torboxarr.env` (gitignored):
```
TORBOX_API_KEY=<get from ~/.env TORBOX_API_KEY on Windows -- do not paste here>
TORBOXARR_DATA_ROOT=/mnt/media/downloads
PUID=1000
PGID=1000
```

The user will provide the API key value. Ask them to paste it into the env file
directly on media-vm rather than through this session:
```bash
ssh media-vm "nano /opt/media/server-stack/compose/torboxarr.env"
```

Verify `.gitignore` covers it:
```bash
grep torboxarr ~/Documents/Gits/server-stack/compose/.gitignore 2>/dev/null || grep '*.env' ~/Documents/Gits/server-stack/.gitignore
```

If not covered, add `torboxarr.env` to the appropriate `.gitignore`.

### Step 1.3 -- Add torboxarr service to docker-compose.yml

Add to `compose/docker-compose.yml` under the indexers profile, after rdt-client:

```yaml
  torboxarr:
    <<: *restart-policy
    image: server-stack/torboxarr:local
    profiles: [indexers]
    container_name: torboxarr
    env_file:
      - ${TORBOXARR_ENV:-./torboxarr.env}
    volumes:
      - /mnt/media/downloads:/mnt/media/downloads
    ports:
      - "8085:8085"
    depends_on:
      - sonarr
      - radarr
```

Also add `TORBOXARR_ENV` reference to `compose/.env` if it has an env var block:
```
TORBOXARR_ENV=./torboxarr.env
```

### Step 1.4 -- Start TorBoxarr

```bash
ssh media-vm "cd /opt/media/server-stack/compose && docker compose --profile indexers up -d torboxarr"
ssh media-vm "docker logs torboxarr 2>&1 | tail -20"
```

Verify it starts cleanly -- should log something like "listening on :8085".

### Step 1.5 -- Add TorBoxarr as second download client in Sonarr

In Sonarr UI: Settings → Download Clients → Add → qBittorrent
- Name: TorBoxarr
- Host: `torboxarr` (container name, since Sonarr is on same Docker network)
- Port: 8085
- Username: (leave blank or use whatever TorBoxarr requires -- check its README)
- Password: (same)
- Category: `sonarr`
- Priority: 2 (rdt-client stays at priority 1 for now)
- Test → Save

Do the same in Radarr with category `radarr`.

Alternatively use the Sonarr/Radarr API to add the download client programmatically.
Check if the API supports this to avoid manual UI steps.

### Step 1.6 -- Verify end-to-end

Trigger a manual search in Sonarr for a missing episode that is not currently
downloading. Watch the flow:
- Sonarr grabs a release
- TorBoxarr receives it (check `docker logs torboxarr`)
- TorBox processes it (check TorBoxarr logs for state transitions)
- File lands at `/mnt/media/downloads/completed/sonarr/<release>/`
- Sonarr imports the file and marks episode as downloaded

This may take a while depending on whether TorBox has the content cached.
If TorBox has it cached, it should be near-instant. If not, it downloads.

Verify hardlinks work:
```bash
# After a successful import, check that the file in /mnt/media/tv is a hardlink
# to the file in /mnt/media/downloads (same inode)
ssh media-vm "stat /mnt/media/tv/<series>/<season>/<episode>.mkv | grep Inode"
ssh media-vm "stat /mnt/media/downloads/completed/sonarr/<release>/<episode>.mkv | grep Inode"
# Inodes should match if hardlinks are working
```

If hardlinks fail (different inodes), check:
- Sonarr Settings → Media Management → "Use Hardlinks instead of Copy" is ON
- Both paths are on the same NFS server (192.168.1.164) -- they should be

### Step 1.7 -- Commit Phase 1 changes

```bash
cd ~/Documents/Gits/server-stack
git add compose/docker-compose.yml
git commit -m "compose: add torboxarr service (Phase 1 parallel setup)"
git push
ssh media-vm "cd /opt/media/server-stack && git pull"
```

### Phase 1 compaction checkpoint

**Before compacting, verify:**
- [ ] TorBoxarr container running and healthy
- [ ] TorBoxarr added as download client in Sonarr and Radarr (priority 2)
- [ ] At least one successful end-to-end download verified
- [ ] Hardlinks confirmed working
- [ ] compose change committed and pushed

**Compact the session now. Use this Phase 2 opening prompt after compaction:**

```
You are Worker 9, continuing Phase 2 of the TorBox migration.
Phase 1 is complete: TorBoxarr is running in parallel alongside rdt-client.
TorBoxarr is priority 2 in Sonarr/Radarr; rdt-client is priority 1.
At least one successful end-to-end download was verified.

Your job now is Phase 2: make TorBoxarr the primary download client and drain
rdt-client's queue.

Repo: https://github.com/xransum/server-stack
media-vm: ssh media-vm (ransum, passwordless sudo)
Compose: /opt/media/server-stack/compose/docker-compose.yml
Sonarr API key: get from /opt/media/server-stack/compose/queue-cleaner.env on media-vm
Radarr API key: same file

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs.

After Phase 2 is done, compact again using the Phase 3 prompt at the bottom
of docs/workers/worker-9-torbox.md.
```

---

## Phase 2 -- Cutover

**Goal**: TorBoxarr becomes the primary download client. rdt-client disabled.
All new grabs go through TorBox.

### Step 2.1 -- Flip download client priorities

In Sonarr: Settings → Download Clients → TorBoxarr → set Priority to 1
In Sonarr: Settings → Download Clients → rdt-client (qBittorrent) → set Priority to 2

Do the same in Radarr.

Or use the API:
```bash
# Get download client list
SONARR_KEY=$(grep SONARR_API_KEY /opt/media/server-stack/compose/queue-cleaner.env | cut -d= -f2)
curl -s -H "X-Api-Key: $SONARR_KEY" http://localhost:8989/api/v3/downloadclient | python3 -c "
import json,sys
for c in json.load(sys.stdin):
    print(c['id'], c['name'], c.get('priority'))
"
```

Then PUT the updated priority for each client.

### Step 2.2 -- Let rdt-client queue drain

Check what is still in rdt-client's queue:
```bash
ssh media-vm "docker cp rdt-client:/data/db/rdtclient.db /tmp/rdt_drain.db && sqlite3 /tmp/rdt_drain.db 'SELECT RdName, RdStatusRaw FROM Torrents ORDER BY Added DESC;'"
```

For any items still actively downloading in rdt-client:
- If `status=downloading` and progressing: let them finish naturally
- If `status=magnet_conversion` or `error`: rdt-blocklist will clean them up
  within 5 minutes (STUCK_PROCESSING_MINUTES=5 is set)

Check Sonarr queue to confirm new grabs are routing through TorBoxarr:
```bash
SONARR_KEY=$(grep SONARR_API_KEY /opt/media/server-stack/compose/queue-cleaner.env | cut -d= -f2)
curl -s -H "X-Api-Key: $SONARR_KEY" 'http://localhost:8989/api/v3/queue?pageSize=50' | python3 -c "
import json,sys
for r in json.load(sys.stdin).get('records',[]):
    print(r.get('downloadClient'), '|', r.get('status'), '|', r.get('title','')[:60])
"
```

New grabs should show `TorBoxarr` as the download client, not `rdt-client`.

### Step 2.3 -- Disable rdt-client as download client

Once the rdt-client queue is empty (or only has items you're OK abandoning):

In Sonarr: Settings → Download Clients → rdt-client → disable (toggle off, do NOT delete yet)
Do the same in Radarr.

This stops new grabs going to rdt-client while keeping the config in case you
need to roll back.

### Step 2.4 -- Verify no rdt-client grabs happening

Watch the queue for a few minutes:
```bash
ssh media-vm "docker logs queue-cleaner 2>&1 | tail -20"
ssh media-vm "docker logs rdt-blocklist 2>&1 | tail -20"
ssh media-vm "docker logs torboxarr 2>&1 | tail -30"
```

TorBoxarr logs should show activity. rdt-client logs should be quiet.

### Phase 2 compaction checkpoint

**Before compacting, verify:**
- [ ] TorBoxarr is priority 1 in Sonarr and Radarr
- [ ] rdt-client is disabled (not deleted) in Sonarr and Radarr
- [ ] New grabs routing through TorBoxarr confirmed
- [ ] rdt-client queue empty or only harmless remnants
- [ ] queue-cleaner and rdt-blocklist still running (they still serve queue-cleaner Modes 1-5)

**Compact the session now. Use this Phase 3 opening prompt after compaction:**

```
You are Worker 9, continuing Phase 3 of the TorBox migration.
Phase 1 complete: TorBoxarr running in parallel.
Phase 2 complete: TorBoxarr is primary, rdt-client disabled in Sonarr/Radarr.

Your job now is Phase 3: deprecate rdt-client and rdt-blocklist, write
torbox-blocklist, clean up the stack, and finalize.

Repo: https://github.com/xransum/server-stack
media-vm: ssh media-vm (ransum, passwordless sudo)
Compose: /opt/media/server-stack/compose/docker-compose.yml
Sonarr API key: get from /opt/media/server-stack/compose/queue-cleaner.env on media-vm
Radarr API key: same file
TorBox API key: in /opt/media/server-stack/compose/torboxarr.env on media-vm

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs.

When all done, update this file's ## Status to "completed", add
## Completion Notes, commit, push, ask for confirmation.
```

---

## Phase 3 -- Deprecate rdt-client + rdt-blocklist, add torbox-blocklist

**Goal**: Remove rdt-client and rdt-blocklist from the stack. Write
torbox-blocklist to handle TorBox-specific stuck/error downloads.
Clean up all env vars and compose references. Commit everything.

### Step 3.1 -- Write torbox-blocklist.py

Create `scripts/torbox-blocklist.py`. This script:
- Polls `GET https://api.torbox.app/v1/api/torrents/mylist` every 2 minutes
- For each torrent with status `stalled` or `error` older than STUCK_MINUTES (default 5):
  - Match against Sonarr/Radarr queue by title + SxxExx fallback (same logic as rdt-blocklist)
  - If matched: blocklist in Sonarr/Radarr + trigger EpisodeSearch/MoviesSearch
  - Delete from TorBox: `DELETE https://api.torbox.app/v1/api/torrents/controltorrent`
    with body `{"torrent_id": <id>, "operation": "delete"}`
  - If no queue match (orphaned): delete from TorBox only
- Also handles cloud cleanup: delete completed TorBox tasks after Sonarr/Radarr
  have imported them (the PR #8 gap in TorBoxarr)
- No SQLite, no Docker socket -- pure REST API calls
- Env vars: `TORBOX_API_KEY`, `SONARR_URL`, `SONARR_API_KEY`, `RADARR_URL`,
  `RADARR_API_KEY`, `STUCK_MINUTES=5`, `DRY_RUN=true`, `POLL_INTERVAL_SECONDS=120`

The script should follow the same patterns as rdt-blocklist.py:
- `log()`, `env_bool()`, `env_int()`, `env_list()` helpers
- `names_match()` + `episode_match()` for queue matching (copy from rdt-blocklist)
- `run_once()` + `main()` structure with poll loop
- `DRY_RUN=true` default -- user flips to false after verifying

Also create:
- `docker/torbox-blocklist/Dockerfile` (same pattern as rdt-blocklist: python:3.13-slim, copy script, root user)
- `docker/torbox-blocklist/example.env` (template, no real values)

### Step 3.2 -- Add torbox-blocklist to compose

Add to `compose/docker-compose.yml` under indexers profile:

```yaml
  torbox-blocklist:
    <<: *restart-policy
    build:
      context: ..
      dockerfile: docker/torbox-blocklist/Dockerfile
    image: server-stack/torbox-blocklist:local
    profiles: [indexers]
    container_name: torbox-blocklist
    env_file:
      - ${TORBOX_BLOCKLIST_ENV:-./torbox-blocklist.env}
    depends_on:
      - sonarr
      - radarr
```

Create `/opt/media/server-stack/compose/torbox-blocklist.env` on media-vm (gitignored):
```
TORBOX_API_KEY=<same key as torboxarr.env>
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=<get from queue-cleaner.env>
RADARR_URL=http://radarr:7878
RADARR_API_KEY=<get from queue-cleaner.env>
DRY_RUN=true
STUCK_MINUTES=5
POLL_INTERVAL_SECONDS=120
```

Build and start:
```bash
ssh media-vm "cd /opt/media/server-stack/compose && docker compose --profile indexers build torbox-blocklist && docker compose --profile indexers up -d torbox-blocklist"
ssh media-vm "docker logs torbox-blocklist 2>&1 | tail -20"
```

Verify it runs in dry-run mode without errors, then flip DRY_RUN=false.

### Step 3.3 -- Remove rdt-client and rdt-blocklist from compose

**Stop the containers first:**
```bash
ssh media-vm "cd /opt/media/server-stack/compose && docker compose --profile indexers stop rdt-client rdt-blocklist"
```

Remove from `compose/docker-compose.yml`:
- The `rdt-client` service block
- The `rdt-blocklist` service block
- The `rdt-client-config` named volume (if no other service uses it)

Also remove from `compose/.env` (if present):
- `RDT_BLOCKLIST_ENV`
- Any rdt-client-specific references

### Step 3.4 -- Remove rdt-client volume (optional, destructive)

The rdt-client SQLite DB is in named volume `server-stack_rdt-client-config`.
Once you're confident the migration is complete and you don't need to roll back:

```bash
ssh media-vm "docker volume rm server-stack_rdt-client-config"
```

**Do NOT do this until you are fully satisfied with TorBoxarr.** It's listed
here for completeness. The user decides when to pull this trigger.

### Step 3.5 -- Clean up gitignore and example env files

Remove any references to rdt-client/rdt-blocklist from:
- `docker/rdt-blocklist/example.env` -- leave the files but note deprecated
- Or remove the directories entirely if preferred

Add to `.gitignore` (if not already covered):
```
compose/torboxarr.env
compose/torbox-blocklist.env
```

### Step 3.6 -- Update docs

Update `docs/workers/state.md`:
- Change `rdt-client` row to show deprecated/removed
- Add `torboxarr` and `torbox-blocklist` rows
- Note TorBox as the active debrid provider

### Step 3.7 -- Final commit

```bash
cd ~/Documents/Gits/server-stack
git add -A
git commit -m "compose: complete TorBox migration -- add torboxarr + torbox-blocklist, remove rdt-client + rdt-blocklist"
git push
ssh media-vm "cd /opt/media/server-stack && git pull"
```

### Phase 3 completion checklist

- [ ] torbox-blocklist.py written and running (DRY_RUN=false)
- [ ] torbox-blocklist detecting stalled/error TorBox tasks correctly
- [ ] rdt-client stopped and removed from compose
- [ ] rdt-blocklist stopped and removed from compose
- [ ] rdt-client volume removed (or deferred -- user decides)
- [ ] All new grabs going through TorBoxarr end-to-end
- [ ] queue-cleaner Modes 1-5 still running (they work at Sonarr/Radarr layer)
- [ ] state.md updated
- [ ] All changes committed and pushed

---

## Rollback plan

If TorBox or TorBoxarr has critical issues during Phase 2 or 3:

1. Re-enable rdt-client in Sonarr/Radarr download clients
2. Set rdt-client back to priority 1
3. Stop torboxarr: `docker compose --profile indexers stop torboxarr`
4. rdt-blocklist is still running throughout -- no change needed
5. Investigate the TorBoxarr issue before retrying

The rdt-client container and volume are kept until Phase 3 Step 3.4,
so rollback is always available until that point.

---

## Known issues to watch for

| Issue | What to do |
|---|---|
| TorBox 429 rate limit hit | TorBoxarr handles internally; grabs queue and retry -- just wait |
| `.torrent` URL not fetched before submit (PR #11) | Check if merged. If not, apply patch manually or use magnet links only |
| TorBox tasks accumulating in cloud | torbox-blocklist handles cleanup after import |
| Hardlinks not working | Verify both paths are same NFS server; check Sonarr hardlink setting |
| TorBoxarr PUID/PGID mismatch | Confirm torboxarr.env has PUID=1000 PGID=1000 |
| TorBoxarr has no staged/incomplete files on restart | It checkpoints to SQLite -- resumes after restart automatically |

---

## Completion Notes

(fill in when done)
