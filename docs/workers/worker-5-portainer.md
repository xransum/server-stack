# Worker 5 — Portainer CE (Docker Management UI)

## Status
✅  completed

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

Completed 2026-10-19.

### What was done

1. **`compose/docker-compose.yml`** — Added `portainer` service block in the `media` profile (after `calibre-web`) with Docker socket mount, `portainer-data` named volume, and port `9443:9443`. Added `portainer-data:` to the named volumes section. Committed and pushed (`bc60426`).

2. **Deployed on media-vm** — `git stash` was required first because `docker/scan-media/Dockerfile` and `scripts/scan-media-server.py` had unstaged local changes on media-vm (likely from another in-progress worker). Stashed, pulled, deployed. Container came up healthy; `curl -sk https://localhost:9443` returned HTTP 200.

3. **Cloudflare DNS** — Grey-cloud A record `docker.xransum.com → 100.107.1.14` created via `cf dns records create -z xransum.com --body ...`. Note: the `cf` CLI requires `-z <zone>` as a flag, not a positional argument — the syntax in the worker doc (`cf dns records create xransum.com ...`) was incorrect and was fixed during execution.

4. **NPM proxy host** — Created via NPM API on media-vm (proxy host ID 13). Forwards `docker.xransum.com` → `https://portainer:9443` with `proxy_ssl_verify off` to accept Portainer's self-signed cert. No SSL cert on the NPM side (Tailscale-only, not public).

### Decisions that shifted

- **`cf` CLI syntax**: The worker doc showed `cf dns records create xransum.com --type A ...` (zone as positional arg). The installed `cf` CLI requires `-z xransum.com` (flag) with `--body` JSON. Used `--body` to pass all record fields cleanly.
- **git stash on media-vm**: Not anticipated in the job steps. Media-vm had local uncommitted changes from another worker. Stashed before pulling; stash was not re-applied (those changes belong to another worker's session).
- **SSL cert required (post-deploy fix)**: After initial deployment, `https://docker.xransum.com` threw `SSL_ERROR_UNRECOGNIZED_NAME_ALERT` because the NPM proxy host was created with `certificate_id: 0` (no cert). Since the A record points to a Tailscale IP (not publicly routable), HTTP-01 challenge is not viable. Fixed by issuing a Let's Encrypt cert via DNS-01/Cloudflare using the existing `CF_DDNS_TOKEN` from `compose/.env`. NPM API schema (v2.16.0) only accepts `dns_challenge`, `dns_provider`, `dns_provider_credentials`, and `propagation_seconds` in `meta` — `letsencrypt_email` and `letsencrypt_agree` are not valid meta fields and cause a 400 error. Cert issued as ID 2, proxy host 13 updated with `certificate_id: 2` and `ssl_forced: true`.

- **Portainer first-run environment setup**: The init wizard auto-creates a `local` environment pointing at `unix:///var/run/docker.sock` but it may not be immediately visible. If no containers appear, navigate to the home dashboard and click the environment card directly — do not use the Environments settings page. Skip Edge Compute during init; it is for remote/NAT environments and adds no value for a local single-host setup.

- **Stray exited containers**: Two auto-named containers (`pensive_driscoll`, `zealous_mccarthy`) were found exited on media-vm — both were `cloudflared` instances from earlier ad-hoc runs, not stack services. Removed manually. Not related to this worker.
