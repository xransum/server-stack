# Shelfmark

On-demand ebook search and grab. Shelfmark lets you search for ebooks via Prowlarr indexers, download them through rdt-client, malware-scan them with the existing ClamAV sidecar, and auto-import clean files into Calibre-Web.

- **Port**: 8084
- **Image**: `ghcr.io/calibrain/shelfmark-lite:latest` (Lite — Prowlarr only, no bundled browser, ~200MB RAM vs ~2GB for standard)
- **Profile**: `indexers`
- **Access**: CF Tunnel at `shelfmark.xransum.com` (same pattern as Sonarr/Radarr)

## Why Shelfmark (not Readarr)

Readarr is deprecated. Shelfmark is in maintenance mode (bug fixes only) and does **not** do author/series monitoring — it is on-demand search-and-grab only. That is accepted and sufficient for this use case.

## Pipeline

```
User (Shelfmark UI)
  → search via Prowlarr indexers
  → grab torrent → rdt-client (qBittorrent mode, category: books)
  → rdt-client downloads to /mnt/media/downloads/books/incoming
  → Shelfmark Custom Script hook: scripts/shelfmark-post-process.py
      → POST to scan-media:8080/scan (ClamAV)
      → clean: mv files to /mnt/media/downloads/books/ready
      → infected/error: exit 1 → Shelfmark marks task Error, files stay for manual review
  → calibre-web watcher (/import = books/ready)
      → calibredb add <file>
      → success: delete source
      → failure: move to /import/failed
```

## Directory layout

All paths are under `/mnt/media/downloads/` (existing NFS mount — no new NFS exports needed). Atomic mv within the same mount keeps imports instant.

| Path | Purpose |
|---|---|
| `/mnt/media/downloads/books/incoming` | rdt-client download target (Shelfmark `INGEST_DIR`) |
| `/mnt/media/downloads/books/ready` | Scanned clean files waiting for Calibre import |
| `/mnt/media/downloads/books/ready/failed` | Files calibredb failed to import — manual review |
| `/mnt/media/books` | Calibre library (never written by Shelfmark or rdt-client) |

## Setup (Shelfmark UI — not committed)

After first start at `http://media-vm:8084`:

1. **Prowlarr**: URL `http://prowlarr:9696`, API key from Prowlarr Settings → General
2. **Torrent client**: qBittorrent mode, URL `http://rdt-client:6500`, same credentials Sonarr/Radarr use, category `books`
3. **Output**: Folder mode, path `/mnt/media/downloads/books/incoming`, organize by Author/Title

### Fallback: native Real-Debrid client

If qBittorrent mode fails (Shelfmark issues #470/#607 — blank "Failed to add torrent" or instant Failed while rdt-client finishes):
- Switch to Shelfmark's native Real-Debrid client
- Provide your RD API key (get from real-debrid.com → My Account → API token)
- Radarr/Sonarr continue using rdt-client unchanged
- Do NOT use the Blackhole client — it hands off and never imports

## Post-process hook (`scripts/shelfmark-post-process.py`)

- Called by Shelfmark after each successful download
- Reads JSON payload from stdin (`paths.final_paths[0]`) or `$1` fallback
- POSTs to `scan-media:8080/scan` with a 280s timeout (Shelfmark kills at 300s)
- Strict fail-closed: only `status == "clean"` passes; `scan_error`, `skipped`, infected, or any exception → exit 1
- On clean: moves ebook files (`.epub`, `.pdf`, `.mobi`, `.azw3`, etc.) to `books/ready` via atomic rename
- On failure: files stay in `books/incoming` for manual review; Shelfmark task shows Error

scan-media does NOT blocklist for Shelfmark (no API to call). Discord notification still fires on infection.

## Calibre-Web watcher (`docker/calibre-watch/calibre-watch-init.sh`)

Runs inside the calibre-web container. Watches `/import` (`books/ready` mount), not `/books` (the library).

- Processes files already present at container startup
- `inotifywait -m -e moved_to -e close_write` for new arrivals
- Waits for file size to stabilise (2s) before importing
- `calibredb add --library-path /books <file>` — sequential, one file at a time
- Success (including "already exists"): deletes source
- Failure: moves to `/import/failed/` and logs it
- Log: `/config/logs/calibre-watch.log` inside the container

## Known risks and gotchas

- **Prowlarr 30s search timeout** (Shelfmark issue #1249, fix status unverified): 1337x/apibay go through Byparr where a cold Cloudflare challenge takes 80–120s. These indexers may time out. Test them first; if they fail consistently, use non-CF-protected indexers (Knaben, Nyaa.si) for ebooks.
- **rdt-client qBittorrent mode unproven**: if downloads consistently fail silently, switch to native RD client (see Fallback above).
- **rdt-blocklist**: shelfmark-sourced torrents are not tracked by Sonarr/Radarr, so rdt-blocklist will not find them in the queue. It operates on the rdt-client DB — confirm it does not choke on unknown download IDs (it should just skip them).
- **scan-media scans the parent directory**: `shelfmark-post-process.py` sends the book path; scan-media scans its parent dir. Shelfmark's Author/Title folder layout means each book is in its own subfolder, so the scan stays scoped. Confirm this in the Shelfmark output settings (Folder mode, Author/Title organization).

## Adding to Nginx Proxy Manager / CF Tunnel

Once Shelfmark authentication is configured, add an NPM proxy host on edge-vm:
- Domain: `shelfmark.xransum.com`
- Forward: `http://192.168.1.169:8084`
- SSL: Let's Encrypt DNS-01 via Cloudflare

Then add a CF Tunnel public hostname in Cloudflare Zero Trust pointing at the NPM host.

**Do not expose Shelfmark publicly before authentication is set up** — its docs warn against exposing it without auth configured.
