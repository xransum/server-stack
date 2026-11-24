# Worker 10 — spotbook MCP server

## Status
⏳ pending

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

(fill in when done)
