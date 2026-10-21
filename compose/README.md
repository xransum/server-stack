# compose/

Docker Compose stack for `media-vm` and `gameservers-vm` on Proxmox.
**Status: active — deployed on media-vm (192.168.1.169).**

---

## Prerequisites

1. Ubuntu 24.04 VM with Docker and Docker Compose v2 installed
2. NFS mounts active for all six `/mnt/media/*` subdirs (see `docs/storage.md`)
3. NVIDIA container toolkit installed in media-vm (for Plex NVENC — defer until GPU passthrough is complete)
4. `.env` file created from `.env.example` with secrets filled in

---

## media-vm stack (`docker-compose.yml`)

### Profiles

| Profile | Services |
|---|---|
| `media` | Plex, Tautulli, Seerr (formerly Overseerr), Calibre-Web, Portainer |
| `indexers` | Sonarr, Radarr, Prowlarr, rdt-client, queue-cleaner, rdt-blocklist |
| `cloudflare-bypass` | Byparr, byparr-unwrap, byparr-proxy-1337x, byparr-proxy-apibay |
| `s3` | Reserved (MinIO runs natively on TrueNAS — no active services) |
| `infra` | dns-updater (deprecated — CF Tunnel replaces this) |

> **Note:** The `proxy` profile (Nginx Proxy Manager, cloudflared, cloudflare-ddns) has been
> moved to `edge-vm`. See `compose/docker-compose.edge.yml` and deploy it there instead.

### Quick start — media-vm

```bash
cd compose/
cp .env.example .env
# edit .env — fill in PLEX_CLAIM and service API keys

# Media + indexers
docker compose --profile media --profile indexers up -d

# Cloudflare bypass indexers (after Prowlarr is configured)
docker compose --profile cloudflare-bypass up -d
```

### Quick start — edge-vm (ingress)

```bash
cd compose/
cp .env.example .env
# edit .env — fill in CLOUDFLARE_TUNNEL_TOKEN, CF_DDNS_TOKEN, DOMAINS

docker compose -f docker-compose.edge.yml up -d
```

### Custom Cardigann definitions

Prowlarr loads custom indexer definitions (byparr-proxy Cardigann YAMLs)
from `./custom-definitions/` (bind-mounted to `/config/Definitions/Custom`
inside the container). Create that directory and copy the definitions in:

```bash
mkdir -p compose/custom-definitions
cp definitions/*.yml compose/custom-definitions/
```

### Service URLs (internal, access via Tailscale or LAN)

| Service | URL |
|---|---|
| Plex | http://192.168.1.169:32400/web |
| Sonarr | http://192.168.1.169:8989 |
| Radarr | http://192.168.1.169:7878 |
| Prowlarr | http://192.168.1.169:9696 |
| Seerr (fka Overseerr) | http://192.168.1.169:5055 |
| Tautulli | http://192.168.1.169:8181 |
| rdt-client | http://192.168.1.169:6500 |
| Calibre-Web | http://192.168.1.169:8083 |
| Portainer | https://192.168.1.169:9443 |
| NPM admin (edge-vm) | http://192.168.1.173:81 |

---

## gameservers-vm stack (`docker-compose.gameservers.yml`)

Run on `gameservers-vm` — a separate Proxmox VM with independent resource
allocation. Game servers should not be able to starve the media stack.

Game servers are managed by **Pelican** (Panel + Wings), not by per-game
compose services. This compose stack only stands up the Panel (web UI + API),
its database/cache, and the Wings node daemon. Individual games are created in
the Panel UI from "eggs" and run as Wings-managed containers. See
`docs/proxmox-compute.md` "Game server workflow" and `docs/migration.md`
Phase 5.5.

### Services

| Service | Role |
|---|---|
| `panel` | Pelican Panel web UI + API (always-on, reverse-proxied) |
| `panel-db` | MariaDB backing the Panel |
| `panel-cache` | Redis cache/queue/session store for the Panel |
| `wings` | Node daemon; runs each game server container from its egg |

Live worlds run on the `gameservers-vm` local disk (Wings data dir). Pelican
pushes scheduled backups to the NAS at `/mnt/media/gameservers` (mounted at
`/mnt/gameservers` on gameservers-vm, backup target only).

### Quick start

```bash
cd compose/
cp .env.example .env
# edit .env — set PELICAN_APP_URL and PELICAN_DB_PASSWORD

# Bring up Panel + DB + cache + Wings
docker compose -f docker-compose.gameservers.yml up -d

# Then: create an admin user in the Panel, register the Wings node
# (Panel -> Nodes), and create each game server from its egg. Per-game
# passwords/tokens/world names are set per-server in the Panel, not in .env.
```

---

## Resolved decisions

| Decision | Resolution |
|---|---|
| Reverse proxy | **Nginx Proxy Manager** — web UI, easy SSL via Let's Encrypt Cloudflare DNS-01 |
| S3 service | **MinIO on TrueNAS** — runs as a native TrueNAS app, not in this stack |

See `docs/proxmox-compute.md` for full tradeoff discussion.
