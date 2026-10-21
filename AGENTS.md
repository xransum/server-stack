# AGENTS.md — server-stack conventions and rules

This file is the authoritative reference for AI agents (OpenCode, Claude,
Cursor, Aider, etc.) working in this repo. Read it before making any changes.

---

## Security Rules — ENFORCED, NO EXCEPTIONS

- **NEVER** commit API keys, tokens, passwords, webhook URLs, or any credentials to this repo
- **NEVER** hardcode secrets in worker completion notes — use placeholders like `<redacted>` or `get from app Settings → General`
- All secrets live in `compose/.env`, `queue-cleaner.env`, `rdt-blocklist.env` on media-vm only
- These files are in `.gitignore` and must never be force-added
- If a secret was accidentally committed: stop, tell the user immediately, do NOT push
- API keys in docs must use format: `get from Sonarr Settings → General → API Key`

---

## Project purpose

Docker Compose stack for a self-hosted Proxmox homelab backed by a TrueNAS NAS
over NFS. Three active VMs: `edge-vm` handles ingress (NPM + cloudflared +
cloudflare-ddns), `media-vm` runs the media stack (Plex, Sonarr, Radarr, etc.),
and `gameservers-vm` runs game servers under Pelican (Panel + Wings). See
`docs/proxmox-compute.md` for VM layout and `docs/workers/state.md` for current
operational state.

The native systemd target (`serverhub`, Debian 12) is **fully decommissioned**
as of 2026-10-21. `services/` unit files are kept as historical reference only.

---

## Repository layout

```
definitions/          Prowlarr custom Cardigann YAML indexer definitions
docker/               Dockerfile per custom service (one dir per service)
  <service>/
    Dockerfile
    example.env
compose/              Docker Compose stack (active production)
  docker-compose.yml
  docker-compose.edge.yml
  docker-compose.gameservers.yml
  .env.example
  README.md
docs/                 One markdown file per service + planning/architecture docs
scripts/              Python scripts for custom services (stdlib only, no pip)
services/             systemd unit files (.service, .timer, @-template units)
```

---

## Naming conventions

| Artifact | Convention | Example |
|---|---|---|
| Service name | `kebab-case` | `byparr-proxy` |
| Script | `scripts/<name>.py` | `scripts/byparr-proxy.py` |
| systemd unit | `services/<name>.service` | `services/byparr-proxy@.service` |
| Env file (server) | `/etc/<name>/<instance>.env` | `/etc/byparr-proxy/apibay.env` |
| Env example (repo) | `scripts/<name>.env.example` | `scripts/byparr-proxy.env.example` |
| Doc | `docs/<name>.md` | `docs/byparr-proxy.md` |
| Dockerfile dir | `docker/<name>/` | `docker/byparr-proxy/` |
| Docker env example | `docker/<name>/example.env` | `docker/byparr-proxy/example.env` |
| Compose service | same as service name | `byparr-proxy-apibay` |

---

## Code style

- **Python**: stdlib only. No pip, no venv, no third-party packages for
  custom scripts. All config via environment variables. Run as `nobody`
  for stateless services (pure HTTP proxy/transform, no filesystem access).
  If a service needs filesystem access, use a dedicated system user.
- **Shell**: avoid interactive prompts. Use
  `echo 'PASSWORD' | sudo -S -p '' -v && sudo <cmd>` for scripted sudo
  on the target server. Write payloads to temp files, scp, then ssh-execute
  (PowerShell mangles inline-quoted JSON).
- **YAML**: 2-space indent, no tabs. Cardigann definitions go in `definitions/`.

---

## Commit message convention

```
service-name: imperative summary of what changed

Optional longer body explaining why, referencing any bugs or gotchas.
```

Examples:
- `byparr-proxy: add apibay instance via byparr-unwrap chain`
- `apibay-byparr: hardcode proxy URL to work around Cardigann encoder`
- `docs: add Docker install sections to all service docs`

---

## Parity rules — ENFORCED

These must be satisfied in the same commit as the triggering change. Do not
defer them to a follow-up commit.

| Trigger | Required update |
|---|---|
| New or changed `scripts/<name>.py` | Update `docker/<name>/Dockerfile` and the relevant service block in `compose/docker-compose.yml` |
| New or changed `services/<name>.service` | Update `compose/docker-compose.yml` service block (restart policy, env, volumes) |
| New or changed env schema (added/removed/renamed vars) | Update `docker/<name>/example.env` AND `compose/docker-compose.yml` `env_file:`/`environment:` section |
| New service doc `docs/<name>.md` | Doc must include both "Native install" and "Docker install" sections. Docker section may say "see compose/README.md" until lab is live but must exist. |
| New Cardigann YAML `definitions/<name>.yml` | Add a note in `docs/byparr-proxy.md` "Adding more indexers" section |

Third-party services (Plex, Sonarr, Radarr, Prowlarr, etc.) use official
upstream Docker images — no Dockerfile needed, but `compose/docker-compose.yml`
must have the service block.

---

## Documentation philosophy

Docs are **decision logs**, not just how-to guides. Future maintainers
(including the original author, six months later) must be able to read a
doc and understand:

1. **Why this service exists** — the problem it solves, not just what it does
2. **Decisions made and alternatives rejected** — with enough context to
   understand why (e.g. "we chose ZFS mirror vdevs over RAIDZ because vdev
   shape is fixed at creation and we need to add drives in pairs without
   rebuilding")
3. **Gotchas discovered the hard way** — with the root cause, not just the
   symptom (e.g. the Cardigann URL-encoder bug, Byparr wrapping non-HTML
   in Firefox plaintext-viewer HTML)
4. **What was explicitly deferred and why** — open decisions must be flagged
   as such with the options and the reason for deferral
5. **Current known limitations** — clearly distinguished from bugs (e.g.
   "1337x solve fails upstream, not our config")

Every significant decision must also be logged in `docs/session-log.md`
with the date, context, and outcome.

---

## Deployment targets

### Native systemd (fully decommissioned — `serverhub`)

Services ran as systemd units on Debian 12. serverhub was physically
decommissioned 2026-10-21 — drives pulled, GPU moved to Proxmox.
`services/` unit files are kept as historical reference only.
See `docs/workers/worker-2-decommission.md`.

### Docker Compose (active production — `edge-vm` + `media-vm` on Proxmox)

**edge-vm** (192.168.1.173, Tailscale 100.106.60.22) — ingress layer, operational as of 2026-10-21.

- SSH: `ssh edge-vm` (homelab key, ransum user, passwordless sudo)
- Compose file: `/opt/edge/server-stack/compose/docker-compose.edge.yml`
- Services: nginx-proxy-manager, cloudflared, cloudflare-ddns
- All traffic (CF tunnel + Tailscale-only grey-cloud) enters here

**media-vm** (192.168.1.169, Tailscale 100.107.1.14) — application stack, operational as of 2026-10-19.

- SSH: `ssh media-vm` (homelab key, ransum user, passwordless sudo)
- Compose files: `/opt/media/server-stack/compose/`
- Active profiles: media, indexers, cloudflare-bypass (proxy profile removed — lives on edge-vm)

---

## Deploy pattern (Windows PowerShell host)

PowerShell's `<<<` herestring and inline JSON quoting are broken. Use this
pattern for anything non-trivial:

```powershell
# Write payload to local temp file
# scp it to server
# ssh-execute on server
scp .\local_script.sh media-vm:/tmp/script.sh
ssh media-vm "bash /tmp/script.sh"
```

For sudo without interactive prompts:
```bash
echo 'PASSWORD' | sudo -S -p '' cp /tmp/file /dest/file
```

---

## Resolved decisions

| Decision | Resolution | Notes |
|---|---|---|
| Reverse proxy | Nginx Proxy Manager (Nginx PM) | Web UI, easy SSL via Let's Encrypt Cloudflare DNS-01 challenge, Docker-native workflow. |
| S3 service | MinIO on TrueNAS Scale | Runs as a native app backed by `/mnt/s3/store`; works with AWS CLI, rclone, and standard S3 SDKs. |
| Compute server CPU/RAM | AMD Ryzen 9 7950X3D + 64GB G.Skill DDR5-6000 CL36 | Ordered Oct 2026. Full BOM and prices in `docs/homelab-hardware.md`. |
| Network switch | Dumb 10GbE switch | TP-Link TL-SX1008 or equivalent. Keep the FIOS gateway for routing; do not buy a MikroTik router. |
| Domain | `xransum.com` | Homelab services live here. `kevin-haas.com` stays pointed at GitHub Pages for the blog. |
| Dynamic DNS for grey-cloud records | cloudflare-ddns container | `favonia/cloudflare-ddns` runs on edge-vm (`docker-compose.edge.yml`). Covers plex.xransum.com and all game server subdomains. Add new subdomains to DOMAINS env var in `/opt/edge/server-stack/compose/.env` on edge-vm and restart. |
| Ingress layer | Dedicated edge-vm | NPM + cloudflared + cloudflare-ddns on edge-vm (192.168.1.173). media-vm is application-only. New services get an NPM proxy host on edge-vm pointing at their LAN IP. |

## Open decisions (do not resolve without updating this file + session log)

| Decision | Options | Notes |
|---|---|---|
| Per-game RAM/CPU limits | Per-game | Set on each Pelican server when it is stood up. Starting estimates in `compose/docker-compose.gameservers.yml`. |
