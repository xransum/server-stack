# Worker 8 — Shelfmark Ebook Pipeline

## Status

⏳ in progress

## Job

Deploy and verify the Shelfmark ebook pipeline on media-vm. Expose it at
`shelfmark.xransum.com` via NPM on edge-vm and CF Tunnel.

Code is already committed. Do not modify committed files unless a bug forces it.

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

## Checklist

- [ ] Create `/mnt/media/downloads/books-incoming` and `books-ready` on media-vm
- [ ] Pull `ghcr.io/calibrain/shelfmark-lite:latest` and start shelfmark container
- [ ] Verify shelfmark running at `http://media-vm:8084`
- [ ] Configure Shelfmark UI (Prowlarr URL + API key, rdt-client credentials, output path)
- [ ] Add NPM proxy host on edge-vm: `shelfmark.xransum.com` → `http://192.168.1.169:8084`, SSL via DNS-01
- [ ] Add CF Tunnel public hostname for `shelfmark.xransum.com` → NPM host
- [ ] Verify `https://shelfmark.xransum.com` loads

---

## Completion Notes

_To be filled in when done._
