# Worker 5 — Portainer CE (Docker Management UI)

## Status
⏳ pending

## Trigger
Independent — can be started anytime. Does not depend on other workers.

---

## Opening Prompt (paste this verbatim to start the session)

```
You are Worker 5. Your only job is adding Portainer CE to the media-vm Docker
Compose stack and making it accessible at docker.xransum.com via Tailscale.
Do not do anything outside this scope.
Read this file for all context you need.
When your job is complete, update the ## Status section of this file to "✅ completed",
add a ## Completion Notes section describing what was done and any decisions that shifted,
commit this file, push it, then ask me to confirm everything looks good before closing.

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs to the repo. Use placeholders only. If you accidentally do, stop immediately and tell the user before pushing.

Context file: docs/workers/worker-5-portainer.md in the xransum/server-stack repo.
```

---

## Infrastructure

- **media-vm**: 192.168.1.169, `ssh media-vm` (homelab key `~/.ssh/homelab`, ransum user, passwordless sudo)
- **media-vm Tailscale IP**: `100.107.1.14`
- **Repo**: `https://github.com/xransum/server-stack` (cloned at `/opt/media/server-stack` on media-vm)
- **Compose files**: `/opt/media/server-stack/compose/`
- **Docker named volumes**: `/var/lib/docker/volumes/` on media-vm
- **NPM**: running at `http://localhost:81` on media-vm, credentials `admin@xransum.com` / password in `~/.env` on Windows as `SSH_PASSWORD`
- **Cloudflare zone**: `xransum.com`, zone ID `8b8f0d48410e46dc68583aefda2f5cb8`

## What Portainer Needs

- Docker socket access (`/var/run/docker.sock`) — gives full container management
- Persistent data volume for Portainer's own DB
- HTTPS on port 9443 (internal, self-signed cert)
- Tailscale-only access — NOT through CF tunnel, NOT a public URL

---

## Job Steps

### Step 1 — Add Portainer to docker-compose.yml

Add to the `media` profile in `/opt/media/server-stack/compose/docker-compose.yml`:

```yaml
  portainer:
    <<: *restart-policy
    image: portainer/portainer-ce:latest
    profiles: [media]
    container_name: portainer
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - portainer-data:/data
    ports:
      - "9443:9443"
```

Add `portainer-data:` to the named volumes section at the bottom of the file.

### Step 2 — Commit and push compose change

```bash
cd ~/Documents/Gits/server-stack
git add compose/docker-compose.yml
git commit -m "compose: add Portainer CE to media profile"
git push
```

### Step 3 — Deploy on media-vm

```bash
ssh media-vm "cd /opt/media/server-stack && git pull && cd compose && docker compose --profile media up -d portainer"
```

Verify:
```bash
ssh media-vm "docker ps | grep portainer"
ssh media-vm "curl -sk -o /dev/null -w '%{http_code}' https://localhost:9443"
```
Expect HTTP 200.

### Step 4 — Create DNS record for docker.xransum.com

Grey cloud A record — points to media-vm Tailscale IP, NOT proxied through Cloudflare:
- Type: `A`
- Name: `docker`
- Content: `100.107.1.14`
- Proxied: `false` (grey cloud)

Use Cloudflare dashboard → xransum.com → DNS → Add record.
Or via `cf` CLI on Windows:
```bash
cf dns records create xransum.com --type A --name docker --content 100.107.1.14 --ttl 1
```

### Step 5 — Add NPM proxy host for docker.xransum.com

SSH into media-vm and run:

```bash
NPM_PASS=$(cat ~/.env 2>/dev/null | grep SSH_PASSWORD | cut -d"'" -f2)
NPM_TOKEN=$(curl -s -X POST http://localhost:81/api/tokens \
  -H 'Content-Type: application/json' \
  -d "{\"identity\":\"admin@xransum.com\",\"secret\":\"$NPM_PASS\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -s -X POST http://localhost:81/api/nginx/proxy-hosts \
  -H "Authorization: Bearer $NPM_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "domain_names": ["docker.xransum.com"],
    "forward_scheme": "https",
    "forward_host": "portainer",
    "forward_port": 9443,
    "certificate_id": 0,
    "ssl_forced": false,
    "allow_websocket_upgrade": true,
    "access_list_id": 0,
    "caching_enabled": false,
    "block_exploits": false,
    "advanced_config": "proxy_ssl_verify off;",
    "enabled": true,
    "locations": [],
    "meta": {"letsencrypt_agree": false, "dns_challenge": false}
  }' | python3 -c "import json,sys; d=json.load(sys.stdin); print('Created ID:', d.get('id'), 'error:', d.get('error','none'))"
```

Note: NPM password is stored in `~/.env` on Windows as `SSH_PASSWORD`.
Since NPM is on media-vm (not Windows), you will need to pass the password directly in the script.
The password is: stored in `~/.env` — do NOT hardcode it in the script, ask the user to provide it.

### Step 6 — Verify

From a device connected to Tailscale:
- Open `https://docker.xransum.com`
- Portainer first-run setup should appear — create an admin user
- All media-vm containers should be visible in the Local environment

---

## Success Criteria

- [ ] Portainer container running on media-vm
- [ ] `docker.xransum.com` DNS A record → `100.107.1.14` (grey cloud)
- [ ] NPM proxy host created for `docker.xransum.com`
- [ ] `https://docker.xransum.com` loads Portainer UI when connected to Tailscale
- [ ] All containers visible in Portainer
- [ ] Compose change committed and pushed to repo

---

## Out of Scope

- Do NOT route docker.xransum.com through CF tunnel
- Do NOT add docker.xransum.com to cloudflare-ddns DOMAINS (Tailscale IP is static)
- Do NOT touch any other Docker services
- Do NOT set up any other monitoring tools
- Do NOT touch game servers, devboxes, or serverhub

---

## Completion Notes

(fill in when done)
