# Worker 7 — edge-vm: Dedicated Ingress Layer

## Goal

Extract `cloudflared`, `nginx-proxy-manager`, and `cloudflare-ddns` from media-vm
into a dedicated `edge-vm`. After this worker, media-vm is purely application stack
(Plex, Sonarr, Radarr, etc.) and all inbound traffic — Cloudflare tunnel and
Tailscale-only — flows through edge-vm.

**Status: ✅ completed**

---

## Why

- media-vm was doing double duty as app host + ingress layer, violating single-responsibility
- Adding new VMs/services meant updating NPM on a VM named "media-vm" — semantically wrong
- A dedicated edge VM makes routing changes isolated and independent of app deployments
- Consistent with the existing `docker.xransum.com` (Tailscale-only) pattern, now generalized

---

## VM Spec

| | |
|---|---|
| Proxmox VM ID | 102 |
| Name | edge-vm |
| OS | Ubuntu 24.04 LTS |
| CPU | 1 vCPU (host type) |
| RAM | 2GB (1GB minimum post-install) |
| Disk | 24GB, VirtIO SCSI, discard+iothread+writeback |
| LAN IP | 192.168.1.173 (static) |
| Tailscale IP | 100.106.60.22 |
| SSH aliases | `edge-vm` (Tailscale), `edge-vm-lan` (LAN) |

---

## Phase 0 — VM ID Renumber

devbox-1 (103) and devbox-template (102) renumbered to free VM ID 102 for edge-vm.
Storage: `data` LVM-thin pool on Proxmox.

| VM | Old ID | New ID |
|---|---|---|
| devbox-template | 102 | 103 |
| devbox-1 | 103 | 104 |

Commands used (run on Proxmox):
```bash
lvrename data/vm-103-disk-0 vm-104-disk-0
lvrename data/vm-103-disk-1 vm-104-disk-1
mv /etc/pve/qemu-server/103.conf /etc/pve/qemu-server/104.conf
sed -i 's/vm-103-disk-/vm-104-disk-/g' /etc/pve/qemu-server/104.conf
pvesm free data:vm-103-disk-0; pvesm free data:vm-103-disk-1

lvrename data/base-102-disk-0 base-103-disk-0
lvrename data/base-102-disk-1 base-103-disk-1
mv /etc/pve/qemu-server/102.conf /etc/pve/qemu-server/103.conf
sed -i 's/base-102-disk-/base-103-disk-/g' /etc/pve/qemu-server/103.conf
```

---

## Phase 1 — edge-vm Provisioned

Ubuntu 24.04 installed manually via Proxmox console (ISO installer). Bootstrap script
deployed via Proxmox relay (sshpass → edge-vm):

- Passwordless sudo for `ransum`
- SSH password auth disabled
- `qemu-guest-agent` installed and enabled
- Docker CE + compose plugin installed, `ransum` added to `docker` group
- Tailscale installed and joined to tailnet

SSH config entries added to `~/.ssh/config` on Windows:
```
Host edge-vm        -> 100.106.60.22 (Tailscale)
Host edge-vm-lan    -> 192.168.1.173 (LAN)
```

---

## Phase 2 — Repo + Compose

Repo cloned to `/opt/edge/server-stack` on edge-vm.

`/opt/edge/server-stack/compose/.env` created with:
- `CLOUDFLARE_TUNNEL_TOKEN` — same token as media-vm (one tunnel, one connector)
- `CF_DDNS_TOKEN` — same Cloudflare API token

`compose/docker-compose.edge.yml` added to repo — contains only:
- `nginx-proxy-manager`
- `cloudflared`
- `cloudflare-ddns`

---

## Phase 3 — NPM Data Migration

NPM volumes exported from media-vm, transferred via Windows, imported to edge-vm:

```bash
# Export (media-vm)
docker run --rm -v server-stack_nginx-pm-data:/data -v /tmp:/backup \
  alpine tar czf /backup/npm-data.tar.gz -C / data
docker run --rm -v server-stack_nginx-pm-letsencrypt:/letsencrypt -v /tmp:/backup \
  alpine tar czf /backup/npm-letsencrypt.tar.gz -C / letsencrypt

# Import (edge-vm)
docker volume create edge-stack_nginx-pm-data
docker volume create edge-stack_nginx-pm-letsencrypt
docker run --rm -v edge-stack_nginx-pm-data:/data -v /tmp:/backup \
  alpine sh -c 'cd / && tar xzf /backup/npm-data.tar.gz'
docker run --rm -v edge-stack_nginx-pm-letsencrypt:/letsencrypt -v /tmp:/backup \
  alpine sh -c 'cd / && tar xzf /backup/npm-letsencrypt.tar.gz'
```

Proxy host backends updated via NPM API (Python script) — all Docker service names
replaced with LAN IPs since edge-vm can't resolve internal Docker DNS of media-vm:

| Hostname | Backend |
|---|---|
| overseerr.xransum.com | 192.168.1.169:5055 |
| sonarr.xransum.com | 192.168.1.169:8989 |
| radarr.xransum.com | 192.168.1.169:7878 |
| prowlarr.xransum.com | 192.168.1.169:9696 |
| tautulli.xransum.com | 192.168.1.169:8181 |
| rdt.xransum.com | 192.168.1.169:6500 |
| calibre.xransum.com | 192.168.1.169:8083 |
| npm.xransum.com | 127.0.0.1:81 |
| plex.xransum.com | 192.168.1.169:32400 |
| docker.xransum.com | 192.168.1.169:9443 (https, ssl verify off) |
| proxmox.xransum.com | 100.77.224.51:8006 (https, ssl verify off) |
| nas.xransum.com | 100.67.119.85:80 |
| pelican.xransum.com | 192.168.1.170:80 |
| gameservers-vm.xransum.com | 192.168.1.170:8080 |

Let's Encrypt DNS-01 certs issued for `proxmox.xransum.com` and `nas.xransum.com`
via NPM API (Cloudflare DNS challenge, `CF_DDNS_TOKEN`).

### Gotcha — NPM SQLite direct edit does NOT regenerate nginx configs

Updating proxy host backends directly in the SQLite database (copying DB out, editing,
copying back) does NOT cause NPM to regenerate its nginx config files in
`/data/nginx/proxy_host/`. The nginx configs still reference old values. Must use the
NPM REST API to update proxy hosts — the API triggers config regeneration.

### Gotcha — NPM volume name prefix

The named volumes in `docker-compose.yml` with `name: server-stack` are prefixed as
`server-stack_nginx-pm-data` (not `nginx-pm-data`). When migrating, export using the
prefixed name. The edge-vm compose project is named `edge-stack`.

### Gotcha — NPM cert API schema

NPM's POST `/api/nginx/certificates` for DNS-01 certs accepts only these meta fields:
```json
{
  "dns_challenge": true,
  "dns_provider": "cloudflare",
  "dns_provider_credentials": "dns_cloudflare_api_token = <token>",
  "propagation_seconds": 30
}
```
Fields `letsencrypt_agree`, `letsencrypt_email` are NOT valid meta properties and
cause a 400 error. The email comes from NPM's stored Let's Encrypt account.

---

## Phase 4 — Cutover

1. Edge stack brought up: `docker compose -f docker-compose.edge.yml up -d`
2. Cloudflared connected to tunnel `8cd27584-61f9-4d09-bf14-09d5573ad366` (4 connectors)
3. **Router port 443** switched from `192.168.1.169` (media-vm) → `192.168.1.173` (edge-vm)
4. **DNS A records** updated (grey-cloud, Tailscale IP → edge-vm `100.106.60.22`):
   - `docker.xransum.com` (was `100.107.1.14`)
   - `proxmox.xransum.com` (was `100.77.224.51`)
   - `nas.xransum.com` (was `100.67.119.85`)

All 11 services verified HTTP 200/401 after cutover.

---

## Phase 5 — media-vm Cleanup

- `nginx-proxy-manager`, `cloudflared`, `cloudflare-ddns` removed from `docker-compose.yml`
- `nginx-pm-data`, `nginx-pm-letsencrypt` removed from volumes section
- `proxy` profile entry removed from header comment, replaced with note pointing to edge
- `docker compose --profile proxy down` run on media-vm to stop the old containers

---

## Portainer Agent

`portainer/agent` added to `docker-compose.edge.yml` (port 9001) so edge-vm's
containers are visible in the single Portainer UI on media-vm — no second Portainer
instance needed.

Registered via Portainer REST API from media-vm:

```bash
# Get token
TOKEN=$(curl -sk -X POST https://192.168.1.169:9443/api/auth \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<get from ~/.env PORTAINER_PASSWORD>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['jwt'])")

# Add environment
curl -sk -X POST "https://192.168.1.169:9443/api/endpoints" \
  -H "Authorization: Bearer $TOKEN" \
  -F "Name=edge-vm" \
  -F "EndpointCreationType=2" \
  -F "URL=tcp://192.168.1.173:9001" \
  -F "GroupID=1" \
  -F "TLS=true" \
  -F "TLSSkipVerify=true" \
  -F "TLSSkipClientVerify=true"
```

Result: Portainer environment ID 6, name `edge-vm`, status healthy.

**Gotcha — agent TLS cert is scoped to container IP:** Portainer verifies the agent's
TLS cert, but the cert is only valid for the container's internal IP (e.g. `172.18.0.5`),
not the host LAN IP. Must use `TLSSkipVerify=true` + `TLSSkipClientVerify=true`.
Without these, Portainer returns "Unable to get environment type" with an x509 cert error.

**Gotcha — URL needs `tcp://` scheme:** Portainer rejects bare `host:port` with
"Unable to parse docker host". Must prefix with `tcp://`.

---

## State After

| VM | Role |
|---|---|
| edge-vm (192.168.1.173, TS 100.106.60.22) | Ingress: cloudflared + NPM + cloudflare-ddns |
| media-vm (192.168.1.169, TS 100.107.1.14) | App stack: Plex, Sonarr, Radarr, etc. |
| gameservers-vm (192.168.1.170) | Game servers (Pelican + Wings) |
| proxmox (192.168.1.168) | Hypervisor |
| truenas (192.168.1.164) | NAS |
