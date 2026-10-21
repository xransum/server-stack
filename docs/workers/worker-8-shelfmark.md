# Worker 8 — Shelfmark Ebook Pipeline

## Status

✅ completed

## Job

Deploy and verify the Shelfmark ebook pipeline on media-vm. Expose it at
`shelfmark.xransum.com` via NPM on edge-vm and CF Tunnel.

---

## Context

Shelfmark is an on-demand ebook search/grab tool. It integrates with Prowlarr
indexers and Direct Download sources, scans downloads with the existing
scan-media ClamAV sidecar, and auto-imports clean files into Calibre-Web via
a polling watcher.

Full design: `docs/shelfmark.md`

Key committed artifacts:
- `compose/docker-compose.yml` — `shelfmark` service block (profile: `indexers`)
- `scripts/shelfmark-post-process.py` — post-process hook (mounted at `/scripts/post_process.py`)
- `docker/calibre-watch/calibre-watch-init.sh` — calibre-web 15s poll importer

---

## Completion Notes

### Infrastructure

**Staging directories (TrueNAS):**
- `/mnt/media/downloads/books/incoming` — Shelfmark INGEST_DIR, rdt-client download target
- `/mnt/media/downloads/books/ready` — post-scan staging, calibre-web poller imports from here
- `/mnt/media/downloads/books/ready/failed` — calibredb import failures for manual review
- Created on TrueNAS directly (NFS root squash prevented creation via media-vm)

**Containers deployed on media-vm:**
- `shelfmark` — healthy, port 8084, Real-Debrid native client (qBittorrent mode fails silently — see gotchas)
- `scan-media` — recreated with `books/incoming` pinhole volume mount
- `calibre-web` — recreated with `books/ready:/import` mount, poll watcher running
- `prowlarr` — explicit DNS `1.1.1.1`/`8.8.8.8` added to avoid ISP SSL interception

**NPM proxy host** (edge-vm, ID 16):
- Domain: `shelfmark.xransum.com` → `http://192.168.1.169:8084`
- No SSL in NPM (CF Tunnel terminates TLS)

**CF Tunnel** (config version 9):
- Added `shelfmark.xransum.com → http://nginx-proxy-manager:80`
- Updated via Cloudflare API using `cf` CLI OAuth token (CF_DDNS_TOKEN lacks tunnel write scope)
- DNS CNAME created manually — token-managed tunnels do NOT auto-create DNS records

**Calibre library mount:**
- `/mnt/media/books:/books:ro` added to shelfmark service so it can read `metadata.db` for dedup

### Calibre-watch poller (key decisions)

- **inotify replaced with 15s poll loop** — inotify is unreliable over NFS when the writer
  (shelfmark container) is a different NFS client on the same host. Cross-client inotify
  events are not guaranteed on Linux.
- **`/import` directory permissions fixed to 777** on TrueNAS — was 755 (owned uid 950),
  preventing the watcher (running as root, NFS-squashed to nobody) from deleting source
  files after import. `/books` was already 777.
- **inotify-tools removed from DOCKER_MOD** — no longer needed after switching to polling.
- **Init script `+x` bit** set in git (`git update-index --chmod=+x`) — linuxserver s6 init
  system requires the script to be executable or it silently skips it at container boot.
- **Bind mount inode issue** — `git pull` after `docker compose up` causes Docker to see
  the old file inode until the container is force-recreated. Always `git pull` BEFORE
  `docker compose up -d` or use `--force-recreate`.

### Shelfmark UI configuration (manual, not committed)

- **Search mode:** Universal
- **Metadata provider:** Open Library (no API key required)
- **Release source:** Prowlarr (`http://prowlarr:9696`, API key from Prowlarr Settings → General)
- **Download client:** Real-Debrid native (qBittorrent mode silently fails — see gotchas)
- **Calibre library path:** `/books/metadata.db`
- **Direct Download:** Z-Library (`https://z-lib.id/` as first mirror), FlareSolverr URL: `http://byparr:8191`

### Custom Prowlarr indexers added

- `compose/custom-definitions/audiobookbay.yml` — AudioBookBay (audiobookbay.lu)
  - Sister site of EBookBay. `ebb.la` presents a cert for `audiobookbay.lu` — hostname
    mismatch causes SSL errors. This definition points at the correct hostname directly.
  - Content: **audiobooks only**, not epubs. Added for completeness but not useful for
    the ebook pipeline.

### Pipeline verified

End-to-end test with "The Player of Games" by Iain M. Banks:
1. Grabbed via Shelfmark (Prowlarr indexer, Real-Debrid native client)
2. Downloaded to `/mnt/media/downloads/books/incoming/`
3. Post-process hook fired → scan-media ClamAV scan → clean
4. Moved to `/mnt/media/downloads/books/ready/`
5. Calibre-web poller picked up within 15s → `calibredb add` → Imported OK → source deleted
6. Book appeared in Calibre-Web library at `https://calibre.xransum.com`

DCC Books 1–8 added manually via TrueNAS file drop into `books/ready` (indexer/source
coverage for popular ebooks via torrents is thin — see gotchas).

### Gotchas

**rdt-client qBittorrent mode fails silently** (Shelfmark issues #470/#607): Shelfmark
reports "Failed to add to qBittorrent" with no further detail. Switch to native
Real-Debrid client in Shelfmark Settings → Download Client. Sonarr/Radarr continue
using rdt-client unchanged.

**Ebook torrent coverage is thin**: Virtually all popular torrent indexers with Books
categories are either CF/JS/DDoS-Guard protected or have sparse ebook content. Practical
sources for epubs are direct download sites (Z-Library, Anna's Archive) not torrents.

**Anna's Archive blocked by DDoS-Guard**: Shelfmark's FlareSolverr bypasser cannot solve
DDoS-Guard manual CAPTCHAs. Anna's Archive is not viable as an automated source.

**Z-Library z-lib.gd IP-blocked from media-vm**: Server IPs are blocked regardless of
headers. Use `z-lib.id` as first mirror (returns 200 from media-vm). `z-lib.gd` works
fine from a residential browser.

**post_process.py must be executable** (`chmod +x` / `git update-index --chmod=+x`):
Shelfmark refuses to call the custom script if it lacks execute permission. The git
mode must be 100755 or the bind-mounted file won't be executable inside the container.

**EBookBay (ebb.la) SSL failure**: `ebb.la` presents a cert issued for `audiobookbay.lu`
— hostname mismatch. Custom `audiobookbay.yml` definition targets the correct hostname
but that site is audiobooks only. No working epub-specific alternative found.

**Manual epub import workflow**: Drop epub files directly into
`/mnt/media/downloads/books/ready/` on TrueNAS. The calibre-web poller imports them
within 15 seconds and deletes the source.
