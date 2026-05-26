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

### Profiles

| Profile | Game |
|---|---|
| `minecraft` | Minecraft Java (PaperMC) |
| `minecraft-bedrock` | Minecraft Bedrock |
| `valheim` | Valheim |
| `palworld` | Palworld |
| `cs2` | Counter-Strike 2 |

Pterodactyl Panel runs without a profile (always-on management UI).

### Quick start (spin up a game server)

```bash
cd compose/
cp .env.example .env
# edit .env — fill in game server passwords, CS2 token, etc.

# Start just Minecraft
docker compose -f docker-compose.gameservers.yml --profile minecraft up -d

# Archive a world (stop container, snapshot on NAS, optionally remove container)
docker compose -f docker-compose.gameservers.yml stop minecraft-java
# Then on NAS: zfs snapshot pool/dev/gameservers/minecraft-java@world-name-$(date +%Y%m%d)
docker compose -f docker-compose.gameservers.yml rm minecraft-java
```

---

## Open decisions

| Decision | Notes |
|---|---|
| Reverse proxy | Caddy / Nginx Proxy Manager / Traefik / nginx. Uncomment the placeholder in `docker-compose.yml` when decided. |
| S3 service | Garage / MinIO / Nextcloud. Uncomment the placeholder in `docker-compose.yml` when decided. |

See `docs/proxmox-compute.md` for full tradeoff discussion.
