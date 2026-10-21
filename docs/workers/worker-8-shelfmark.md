# Worker 8 — Shelfmark Ebook Pipeline

## Status

✅ completed

## Job

Deploy and verify the Shelfmark ebook pipeline on media-vm. Expose it at
`shelfmark.xransum.com` via NPM on edge-vm and CF Tunnel.

---

## Context

Shelfmark is an on-demand ebook search/grab tool. It integrates with Prowlarr
indexers, downloads via rdt-client (qBittorrent mode, category `books`), scans
downloads with the existing scan-media ClamAV sidecar, then auto-imports clean
files into Calibre-Web via the inotify watcher.

Full design: `docs/shelfmark.md`

Key committed artifacts:
- `compose/docker-compose.yml` — `shelfmark` service block (profile: `indexers`)
- `scripts/shelfmark-post-process.py` — post-process hook (mounted read-only at `/scripts/post_process.py`)
- `docker/calibre-watch/calibre-watch-init.sh` — calibre-web inotify watcher (already deployed)

---

## Completion Notes

**Path correction (before deployment):** The original design used flat staging
dirs `downloads/books-incoming` and `downloads/books-ready`. Updated to
`downloads/books/incoming` and `downloads/books/ready` to match the existing
`downloads/<category>/` layout convention used by Sonarr/Radarr. Also added
`BOOKS_READY_DIR` explicitly to the shelfmark compose environment block so
staging paths are visible in one place without reverse-engineering script
defaults.

**Directories created on TrueNAS** (NFS root squash prevented creation via
media-vm):
- `/mnt/media/downloads/books/incoming`
- `/mnt/media/downloads/books/ready`
- `/mnt/media/downloads/books/ready/failed`

**Containers deployed on media-vm** (`--profile indexers --profile media`):
- `shelfmark` — healthy, port 8084
- `scan-media` — recreated with corrected `books/incoming` volume mount
- `calibre-web` — recreated with corrected `books/ready:/import` mount

**NPM proxy host** (edge-vm, ID 16):
- Domain: `shelfmark.xransum.com`
- Forward: `http://192.168.1.169:8084`
- No SSL in NPM (CF Tunnel terminates TLS, same pattern as all other CF tunnel services)

**CF Tunnel** (tunnel `8cd27584-...`, config version 9):
- Added `shelfmark.xransum.com → http://nginx-proxy-manager:80` ingress rule
- Updated via Cloudflare API using `cf` CLI OAuth token (CF_DDNS_TOKEN lacks
  tunnel write scope)

**DNS CNAME** created in Cloudflare zone `xransum.com`:
- `shelfmark` → `8cd27584-61f9-4d09-bf14-09d5573ad366.cfargotunnel.com` (proxied)
- Note: CF tunnel token-managed tunnels do NOT auto-create DNS records — must
  be created manually (unlike named tunnels via `cloudflared tunnel route dns`)

**Verified:** `https://shelfmark.xransum.com` → HTTP 200 (tested via direct
Cloudflare IP with Host header while local DNS propagated)

**Remaining (manual UI steps — not automated):**
- Configure Shelfmark UI at `http://media-vm:8084` or `https://shelfmark.xransum.com`:
  1. Prowlarr: `http://prowlarr:9696`, API key from Prowlarr Settings → General
  2. Torrent client: qBittorrent mode, `http://rdt-client:6500`, same creds as Sonarr/Radarr, category `books`
  3. Output: Folder mode, `/mnt/media/downloads/books/incoming`, organize by Author/Title
  4. Custom Script: `/scripts/post_process.py` with JSON payload enabled (already set via env vars in compose)
- If qBittorrent mode fails silently, fall back to native Real-Debrid client (see `docs/shelfmark.md`)
