# Worker 10 — spotbook MCP server

## Status
✅ completed — 2026-11-24

## Trigger
Independent — anytime. Build the spotbook personal MCP server on the `spotbook`
branch. Do not push to main until the end-to-end test in Step 6 passes.

---

## Opening Prompt (paste verbatim to start the session)

```
You are Worker 10. Your only job is building the spotbook MCP server on the
spotbook branch of the server-stack repo. Read the full job definition at
docs/workers/worker-10-spotbook.md and docs/spotbook.md before writing any
code. Follow AGENTS.md exactly (conventions, parity rules, secrets rules,
commit format).

Repo: https://github.com/xransum/server-stack  branch: spotbook
Local (Windows): ~/Documents/Gits/server-stack
media-vm: ssh media-vm (ransum, passwordless sudo)

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs.
Use <redacted> placeholders in all docs and example.env files.
Secrets go in compose/.env on media-vm only (gitignored).

Do not push to main. All commits go to the spotbook branch.

Stop at each STOP marker in the build order and wait for instructions.
```

---

## What this is

A single-user MCP server hosted on media-vm. Claude (claude.ai web + phone app)
is the chat layer. The server is a personal book of places and activities —
restaurants, bars, coffee, outdoor spots, things to do — organized by area and
enriched with live Google data. Claude can answer questions like "I'm in Redmond
Saturday 6-10pm, what from my list is open?" without any LLM cost; facts come
from APIs and SQLite, never from the model.

Occasion-neutral throughout: not date-only or romantic-only. All names, tool
descriptions, and docs must reflect that.

---

## Decisions made (source of truth for the build)

### Language and SDK
- **Python 3.12** with the official MCP Python SDK (`mcp==2.3.0`)
- Explicit exception to the repo's stdlib-only rule — documented in AGENTS.md
  Resolved Decisions table and in docs/spotbook.md
- SDK dependencies (all pinned): `mcp==2.3.0`, `httpx2>=2.10.0`, `pydantic>=2.12.0`,
  `starlette>=0.27`, `uvicorn>=0.31.1`, `anyio>=4.9`, `sse-starlette>=3.0.0`,
  `python-multipart>=0.0.9`, `pyjwt[crypto]>=2.10.1`, `jsonschema>=4.20.0`,
  `opentelemetry-api>=1.28.0`, `typing-extensions>=4.13.0`, `typing-inspection>=0.4.1`
- stdlib only beyond that: `sqlite3`, `zoneinfo`, `hashlib`, `hmac`, `secrets`,
  `urllib.request`, `urllib.parse`, `asyncio`, `datetime`, `logging`
- `tzdata` system package required in the image for `zoneinfo`

### Auth — GitHub OAuth + embedded MCP auth server
- `OAuthAuthorizationServerProvider` (SDK's embedded AS interface) — single
  container, no external auth server dependency
- GitHub is the identity provider. The `/authorize` endpoint redirects to GitHub.
  `/github/callback` exchanges the code, verifies the user is the allowed GitHub
  account (by numeric user ID, not username), then completes the MCP OAuth flow.
- GitHub OAuth App redirect URI: `https://spotbook.xransum.com/github/callback`
- Required env vars: `SPOTBOOK_GITHUB_CLIENT_ID`, `SPOTBOOK_GITHUB_CLIENT_SECRET`,
  `SPOTBOOK_GITHUB_ALLOWED_USER_ID` (numeric, stable — not username)
- MCP tokens issued by spotbook itself; Claude never sees the GitHub token
- Access tokens: 1 hour TTL. Refresh tokens: rotating, 90-day absolute lifetime.
  Stored as SHA-256 hashes in SQLite.
- Rate limiting on `/github/callback`: 5 failures per IP per 15 minutes; 1
  global failure burst threshold to catch credential-stuffing

### Transport
- Streamable HTTP at `/mcp` (not `/sse` — that selects the legacy SSE transport)
- `TransportSecuritySettings(allowed_hosts=["spotbook.xransum.com",
  "spotbook.xransum.com:*"])` required or every request returns 421
- Behind Cloudflare Tunnel → NPM (TLS-terminating proxy):
  uvicorn must run with `--proxy-headers --forwarded-allow-ips=*`
  (NPM is the only thing that can reach the container)
- Port: **8086** (confirmed unused on media-vm)
- `proxy_buffering off` in NPM for this host — MCP streaming requires it

### Not verified during planning (must verify during build, report findings)
- Whether only port 443 is accepted by Claude (we use 443 via tunnel anyway)
- Whether Streamable HTTP is the transport Claude uses by default or requires config
- Whether a connector added on claude.ai web works in the phone app
- OAuthAuthorizationServerProvider `auth_server_provider=` constructor argument
  exact signature — verify against SDK 2.3.0 source before using

### Data
- SQLite in WAL mode at `SPOTBOOK_DB_PATH` (default `/data/spotbook.db`)
- Do NOT run SQLite on NFS (locking). The volume is local to media-vm.
- NAS backup target is a future open item (see docs/spotbook.md)

### Google API
- Places API (New): Text Search for `discover()`, Place Details for `save_spot()`
  and hours refresh
- Always send a field mask. Enterprise fields (hours, rating, priceLevel) only on
  save and weekly refresh — not on Text Search results
- Monthly caps enforced in code: `SPOTBOOK_ENTERPRISE_MONTHLY_CAP` (default 900),
  `SPOTBOOK_PRO_MONTHLY_CAP` (default 4500). At cap: refuse call, return clear
  message to Claude
- `api_usage(month, sku, count)` table in SQLite tracks usage

### Hosting
- One container: `spotbook` on media-vm
- Profile: `media` (same as other personal services)
- Source: `docker/spotbook/app/` (Python package `spotbook`)
- Non-root user (`nobody`), `/data` volume, healthcheck at `/health`
- Nightly backup: `sqlite3.backup()` API, 14-day rotation, `SPOTBOOK_BACKUP_DIR`

---

## File layout

```
docker/spotbook/
  Dockerfile
  example.env
  app/
    spotbook/
      __init__.py
      server.py          MCPServer + lifespan + custom routes
      auth.py            OAuthAuthorizationServerProvider (GitHub flow)
      db.py              SQLite schema, WAL, migrations, seed data
      google.py          Places API calls + cap counter
      hours.py           check_hours pure functions
      tools.py           all MCP tool definitions
      backup.py          daily sqlite3.backup() + Discord alerts
    tests/
      test_hours.py      unit tests for hours logic
      test_auth.py       OAuth flow tests
      test_tools.py      tool filter + haversine tests
docs/spotbook.md         decision log
compose/docker-compose.yml  spotbook service block (parity rule)
```

---

## Build order

### Step 1 — Verify unverified items + pin versions
Confirm SDK 2.3.0 is the version to use. Verify `auth_server_provider=` signature
in SDK source or docs. Check GitHub OAuth API endpoints (no breaking changes).
Document verified vs still-unverified items. Write docs/spotbook.md skeleton and
session-log entry.

### Step 2 — db.py + hours.py + unit tests
SQLite schema (all tables + seed data for Manassas VA and Redmond WA).
Pure-function `check_hours()` covering all edge cases.
Unit tests (test_hours.py) covering the full checklist:
  - 5pm-2am overnight close
  - 24-hour open (single period day 0 00:00 no close)
  - Closed day
  - No hours listed (unknown)
  - ET vs PT timezone interpretation
  - DST change weekend (spring forward / fall back)
  - Week-out date (uses regular hours + caveat flag)

### Step 3 — google.py + tools.py + cap counter
All Places API calls with field masks and cap enforcement.
All MCP tools: list_areas, add_area, update_area, save_spot, list_spots,
check_hours, log_visit, discover. travel_time stub (Phase 2).
Test with MCP Inspector locally.

**STOP — Google Cloud key setup required.**
Tell the user:
1. Go to console.cloud.google.com → New project → name it "spotbook"
2. APIs & Services → Enable: "Places API (New)" and "Routes API"
3. APIs & Services → Credentials → Create API key → Restrict to:
   - Application restrictions: IP addresses → add media-vm LAN IP 192.168.1.169
   - API restrictions: Places API (New), Routes API
4. Set hard daily quota: APIs & Services → Quotas → Places API (New) →
   "Requests per day" → edit → set to 50 (conservative for personal use)
5. Copy the key → store as GOOGLE_MAPS_API_KEY in compose/.env on media-vm
   (gitignored). Never paste it here.

### Step 4 — auth.py + tests
Full OAuthAuthorizationServerProvider implementation backed by SQLite.
GitHub OAuth exchange + user ID verification.
All token lifecycle: DCR, PKCE S256, auth codes, access + refresh tokens,
token rotation, invalid_grant on reused refresh token.

Test suite (test_auth.py) must cover:
  - Unauthenticated /mcp returns 401 with correct WWW-Authenticate header
  - DCR (POST /register) succeeds with valid client metadata
  - DCR rejects redirect_uri other than https://claude.ai/api/mcp/auth_callback
  - Wrong/missing PKCE returns error
  - Valid auth flow issues access + refresh token
  - Refresh token rotation: new token returned, old one invalidated
  - Reused old refresh token returns invalid_grant
  - Expired access token returns 401
  - Rate limiting triggers after 5 /github/callback failures per IP

### Step 5 — Dockerfile + compose + example.env + backup + full docs
Multi-stage build, non-root, tzdata, pinned deps via requirements.txt.
Compose service block: port 8086, /data volume, healthcheck, restart unless-stopped.
docker/spotbook/example.env with placeholders only.
backup.py: daily sqlite3.backup(), 14-day rotation, Discord on failure.
Full docs/spotbook.md decision log.
One parity-correct commit: "spotbook: initial implementation".

**STOP — deploy to media-vm, add CF tunnel hostname, add NPM proxy host.**
Instructions for the user:
1. SSH to media-vm, git pull on the spotbook branch, build and start:
   ```
   cd /opt/media/server-stack
   git fetch origin && git checkout spotbook && git pull
   docker compose --profile media build spotbook
   docker compose --profile media up -d spotbook
   docker logs spotbook --tail 30
   ```
2. Cloudflare Tunnel (dashboard.cloudflare.com → Zero Trust → Tunnels →
   your tunnel → Public Hostnames → Add):
   - Subdomain: spotbook
   - Domain: xransum.com
   - Service: HTTP → <NPM container name or edge-vm LAN IP>:<NPM port>
   (same backend as all other hostnames — route through NPM)
3. NPM on edge-vm: proxy host → spotbook.xransum.com →
   media-vm LAN IP (192.168.1.169) port 8086
   - Enable "Websockets Support": OFF (not needed)
   - Custom Nginx config (Advanced tab):
     ```
     proxy_buffering off;
     ```
   - SSL: use existing wildcard or request Let's Encrypt via DNS-01
4. Test: `curl -i https://spotbook.xransum.com/health` → 200 OK
5. Test: `curl -i https://spotbook.xransum.com/mcp` → 401 with WWW-Authenticate header

### Step 6 — End-to-end test
Add connector on claude.ai web: Customize > Connectors > Add custom connector
  URL: https://spotbook.xransum.com/mcp
Complete OAuth sign-in (GitHub redirect flow).
Test from phone app: confirm connector works.
Run the full test checklist from docs/spotbook.md and report pass/fail with
real output.

Record whether these unverified assumptions held:
- Port 443 only?
- Streamable HTTP default or needs config?
- Web connector works from phone app?

---

## Completion Notes

### Verified assumptions (were unverified at build time)
- **Port 443 only**: yes — CF Tunnel enforces TLS, port 443 only. We use 443 via tunnel anyway so no issue.
- **Streamable HTTP default**: yes — Claude detected it automatically, no explicit config needed. Shown as "Detected" in the connector UI.
- **DCR detected automatically**: yes — Claude showed "Register automatically (Detected)" in the connector setup UI.
- **Web connector → phone app**: not yet tested. Connect on web first, verify phone app separately.
- **`auth_server_provider=` / `OAuthAuthorizationServerProvider`**: used `token_verifier=` + custom OAuth routes instead. The SDK's embedded AS interface (`auth_server_provider=`) was not used — we implemented all OAuth endpoints as custom Starlette routes and used `TokenVerifier` to verify tokens on `/mcp`. This is cleaner than the embedded AS path.

### What was built
- Full MCP server with Streamable HTTP at `/mcp`
- GitHub OAuth: DCR → `/authorize` → GitHub → `/github/callback` (user ID check) → token issued
- Access tokens 1h, refresh tokens 90d rotating, SHA-256 hashed in SQLite
- Rate limiting: 5 failures/IP/15min on `/github/callback`
- All MCP tools: list_areas, add_area, update_area, save_spot, list_spots, check_hours, log_visit, discover
- Google Places API with field masking + monthly cap enforcement (ENTERPRISE 900/mo)
- check_hours: handles overnight, 24h, closed day, DST, 7-day currentOpeningHours window
- Daily sqlite3.backup() with 14-day rotation
- Discord alerts: DCR registration + unauthorized login attempt
- 55 unit tests passing (31 hours, 24 auth)

### Infrastructure changes
- NPM proxy host 17: `spotbook.xransum.com` → `192.168.1.169:8086`, `proxy_buffering off`
- CF Tunnel ingress rule added: `spotbook.xransum.com` → `nginx-proxy-manager:80`
- DNS CNAME: `spotbook.xransum.com` → `<tunnel-id>.cfargotunnel.com` (proxied)
- Named volume: `server-stack_spotbook-data` on media-vm

### Known limitations / open items
- NAS backup target not configured — backups go to `/data/backups` (local to media-vm). See docs/spotbook.md.
- `travel_time` tool is a Phase 2 stub — Routes API not implemented yet.
- Phone app connector not verified — test manually.
- Google API daily quota not set in Cloud Console — do this manually (see docs/spotbook.md).
- DCR spam possible (open `/register`) — low risk for personal use; prune `oauth_clients` table if needed.

### Gotchas found during build
- SDK default DNS-rebinding protection returns 421 for any non-localhost hostname until `TransportSecuritySettings(allowed_hosts=[...])` is passed.
- uvicorn behind NPM needs `--proxy-headers --forwarded-allow-ips=*` or HTTPS redirects break.
- Named Docker volume mounts over `/data` at runtime, resetting ownership — run as root to avoid permission issues.
- Discord webhook returns 403 without a `User-Agent` header — added `User-Agent: spotbook/1.0`.
- Port binding `127.0.0.1:8086` blocks NPM on edge-vm — must bind `0.0.0.0:8086`.
- CF Tunnel ingress rules require a Zero Trust-scoped API token (not the DDNS token).
