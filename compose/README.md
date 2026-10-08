# compose/

Docker Compose stack for the lab deployment target (`media-vm` and
`gameservers-vm` on Proxmox). **Status: untested — awaiting lab hardware.**

Native systemd on `serverhub` is current production. See
`docs/migration.md` for the cutover plan.

---

## Prerequisites

1. Debian 12 VM with Docker and Docker Compose v2 installed
2. NFS mounts active (see `docs/storage.md` and `docs/proxmox-compute.md`)
3. NVIDIA container toolkit installed in media-vm (for Plex NVENC)
4. `.env` file created from `.env.example` with secrets filled in

---

## media-vm stack (`docker-compose.yml`)

### Profiles

| Profile | Services |
|---|---|
| `media` | Plex, Tautulli, Overseerr |
| `indexers` | Sonarr, Radarr, Prowlarr, rdt-client, queue-cleaner |
| `cloudflare-bypass` | Byparr, byparr-unwrap, byparr-proxy-1337x, byparr-proxy-apibay |
| `proxy` | Cloudflare Tunnel (reverse proxy TBD — see open decisions) |
| `s3` | S3 service placeholder (TBD — see open decisions) |
| `infra` | dns-updater (deprecated if CF Tunnel is active) |

### Quick start (full media stack)

```bash
cd compose/
cp .env.example .env
# edit .env — fill in PLEX_CLAIM, CLOUDFLARE_TUNNEL_TOKEN, API keys

docker compose --profile media --profile indexers --profile cloudflare-bypass --profile proxy up -d
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
| Plex | http://media-vm:32400/web |
| Sonarr | http://media-vm:8989 |
| Radarr | http://media-vm:7878 |
| Prowlarr | http://media-vm:9696 |
| Overseerr | http://media-vm:5055 |
| Tautulli | http://media-vm:8181 |
| rdt-client | http://media-vm:6500 |

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
pushes scheduled backups to the NAS `dev/gameservers` dataset (mounted at
`/mnt/gameservers`, backup target only), which also keeps ZFS snapshots.

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

## Open decisions

| Decision | Notes |
|---|---|
| Reverse proxy | Caddy / Nginx Proxy Manager / Traefik / nginx. Uncomment the placeholder in `docker-compose.yml` when decided. |
| S3 service | Garage / MinIO / Nextcloud. Uncomment the placeholder in `docker-compose.yml` when decided. |

See `docs/proxmox-compute.md` for full tradeoff discussion.
