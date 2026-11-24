# spotbook

Personal MCP server for a book of places and activities. Claude (claude.ai)
calls it as a custom connector to answer questions like "what from my list is
open in Redmond Saturday 6-10pm?" Facts come from SQLite and the Google Places
API — never from the LLM. Code decides open/closed, not Claude.

---

## Why it exists

The problem: Claude has no memory of places I want to go, and every time I ask
"what should we do in Manassas this weekend?" it makes things up. I want a
curated personal list that Claude can query with real hours data.

The constraints that shaped the design:
- No Anthropic API calls — zero LLM cost per query
- Single user — no multi-tenant complexity
- Self-hosted — data stays on my own infrastructure
- Mobile-first — the main client is the Claude iPhone app

---

## Decisions and alternatives rejected

### MCP transport: Streamable HTTP at `/mcp`

Chosen because Claude connectors require HTTPS and a path that does not end in
`/sse` (which selects the legacy SSE transport). Streamable HTTP is the current
MCP spec transport.

**SDK gotcha**: `streamable_http_app()` defaults to allowing only localhost.
Behind any real hostname it returns `421 Misdirected Request` until
`TransportSecuritySettings(allowed_hosts=["spotbook.xransum.com",
"spotbook.xransum.com:*"])` is passed. This is a DNS-rebinding protection and
is not negotiable.

**Proxy gotcha**: behind a TLS-terminating proxy (NPM → uvicorn), uvicorn must
run with `--proxy-headers --forwarded-allow-ips=*` or its redirects point at
`http://` and Claude refuses to follow them. NPM must have `proxy_buffering off`
for the MCP streaming responses to work.

### Auth: GitHub OAuth + embedded MCP auth server

**What was considered:**

1. **MCP Tunnel (Anthropic-hosted)** — Enterprise preview only, not available
   to individual accounts. Rejected: not accessible.

2. **Static header auth (custom header)** — Supported in beta for limited
   org accounts only, not for individual Claude users. Rejected: not accessible.

3. **`client_credentials` grant** — Not supported by Claude connectors at all.
   Rejected: protocol mismatch.

4. **Cloudflare Access** — Would gate the entire hostname. The MCP OAuth dance
   requires `/authorize` to load in the user's browser and `/register` + `/token`
   to be reachable by Claude without a Cloudflare Access token. Interaction with
   the MCP OAuth discovery flow is unverified. Rejected: too many unknowns.

5. **Passphrase form** — Self-hosted, no external dependency, works. Rejected
   in favor of GitHub because GitHub re-auth is one tap on mobile (already
   logged in), no credential to remember or rotate, and GitHub's own security
   (2FA, session management) covers the identity layer for free.

6. **GitHub OAuth + embedded AS (chosen)** — Claude performs MCP OAuth
   discovery, registers a DCR client, redirects to `/authorize`. The server
   redirects to GitHub. The user taps "Authorize" (already logged in on phone).
   GitHub redirects to `/github/callback`. The server verifies the GitHub user
   ID against `SPOTBOOK_GITHUB_ALLOWED_USER_ID`, issues its own MCP access +
   refresh tokens, and redirects Claude to `https://claude.ai/api/mcp/auth_callback`.
   Claude never sees the GitHub token. MCP tokens are stored as SHA-256 hashes
   in SQLite with 1-hour access / 90-day refresh lifetime and rotation on use.

**Why embedded AS and not external (Keycloak etc.):** The MCP SDK's
`OAuthAuthorizationServerProvider` interface covers all the protocol mechanics
(DCR, PKCE, token lifecycle). Running a separate auth server for one personal
user adds infrastructure complexity with no benefit.

**SDK note:** The SDK docs warn against `auth_server_provider=` for new servers
and recommend the AS/RS separation model (external AS + `TokenVerifier` only).
That guidance targets multi-tenant production deployments. For a single-user
personal server with GitHub as the IdP and no separate auth service, the
embedded path is the right pragmatic choice. The SDK explicitly keeps
`auth_server_provider=` working and provides `ProviderTokenVerifier` for it.

### Language: Python with MCP SDK

Breaks the repo's stdlib-only rule for scripts. Documented as explicit exception
in AGENTS.md Resolved Decisions table. Justification: this is an application,
not a script. The MCP SDK, an async HTTP server (Starlette/uvicorn), and OAuth
are not achievable with stdlib alone. All other dependencies minimized to what
the SDK already pulls in; no extra HTTP client, no ORM, no JWT library beyond
`pyjwt` (SDK dependency).

### Google API: Places API (New) only

Places API (New) has per-SKU free tiers (Essentials 10k, Pro 5k, Enterprise 1k
per month). We use Enterprise fields (hours, rating, priceLevel) only on
`save_spot()` and weekly refresh, not on Text Search results. A local monthly
counter per SKU in SQLite enforces caps before any Google call. At cap, the tool
returns a clear message to Claude instead of calling Google.

Routes API (`travel_time` tool) is Phase 2. Pricing is unverified (the Google
pricing page blocks automated fetching); document as open item.

### Hosting: single container on media-vm

One container named `spotbook`, port 8086, profile `media`. Exposed via the
existing CF Tunnel → NPM → media-vm LAN IP chain used by all other services.
No new infrastructure.

---

## Gotchas discovered

- **421 Misdirected Request**: SDK default DNS-rebinding protection. Must pass
  `transport_security=` with `allowed_hosts`. See Transport section above.
- **Proxy headers**: uvicorn behind NPM must have `--proxy-headers
  --forwarded-allow-ips=*` or HTTPS redirects break.
- **`proxy_buffering off`** in NPM: required for streaming MCP responses.
- **`auth_server_provider=` deprecation warning**: intentionally using this
  interface. The SDK does not remove it; `ProviderTokenVerifier` exists for it.
  The warning is a design guide, not a prohibition.
- **GitHub user ID vs username**: always use the numeric `id` field from
  `api.github.com/user`, not the `login` (username). Usernames can be changed;
  IDs are permanent. Store in `SPOTBOOK_GITHUB_ALLOWED_USER_ID`.

---

## Deferred / open items

| Item | Status | Notes |
|---|---|---|
| NAS backup target | Open | `SPOTBOOK_BACKUP_DIR` defaults to local path on media-vm (`/data/backups`). Proper target: TrueNAS dataset `media/backups/spotbook` exported via NFS to media-vm. Manual TrueNAS step. Document in notes when done. |
| `travel_time` tool | ✅ implemented | Routes API `computeRouteMatrix`. Always Essentials SKU (no traffic routing). Cached 24h. |
| Routes API pricing | ✅ verified 2026-11-24 | TRANSIT = Essentials (not Pro/Enterprise). No traffic routing = always Essentials. See billing notes below. |
| Port 443 only? | Unverified until deploy | We use 443 via CF Tunnel anyway. |
| Streamable HTTP default? | Unverified until deploy | May need explicit config in Claude connector. |
| Phone app connector? | Unverified until deploy | Claude web connector may or may not sync to phone app. Test in Step 6. |
| Cloudflare IP restriction | Optional hardening | Restrict `/mcp` and `/token` to `160.79.104.0/21` (Anthropic egress) in CF Tunnel or firewall rules. `/authorize` must stay open (browser-loaded). |

---

## Configuration reference

All secrets in `compose/.env` on media-vm only (gitignored). Never commit.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Yes | — | Restricted to Places API (New) + Routes API in Cloud Console |
| `SPOTBOOK_PUBLIC_BASE_URL` | Yes | — | `https://spotbook.xransum.com` — exact MCP URL as entered in Claude |
| `SPOTBOOK_GITHUB_CLIENT_ID` | Yes | — | From GitHub OAuth App settings |
| `SPOTBOOK_GITHUB_CLIENT_SECRET` | Yes | — | From GitHub OAuth App settings |
| `SPOTBOOK_GITHUB_ALLOWED_USER_ID` | Yes | — | Your numeric GitHub user ID (`curl https://api.github.com/users/<login>` → `id` field) |
| `SPOTBOOK_DB_PATH` | No | `/data/spotbook.db` | Local volume — not NFS |
| `SPOTBOOK_BACKUP_DIR` | No | `/data/backups` | See NAS open item above |
| `SPOTBOOK_ESSENTIALS_MONTHLY_CAP` | No | `8000` | Places Essentials cap (free tier: 10,000) |
| `SPOTBOOK_PRO_MONTHLY_CAP` | No | `4000` | Places Pro cap (free tier: 5,000) |
| `SPOTBOOK_ENTERPRISE_MONTHLY_CAP` | No | `800` | Places Enterprise cap (free tier: 1,000) |
| `SPOTBOOK_ROUTES_ESSENTIALS_MONTHLY_CAP` | No | `8000` | Routes Essentials cap (free tier: 10,000) |
| `SPOTBOOK_ROUTES_PRO_MONTHLY_CAP` | No | `4000` | Routes Pro cap (free tier: 5,000) — unused, no traffic routing |
| `SPOTBOOK_ROUTES_ENTERPRISE_MONTHLY_CAP` | No | `800` | Routes Enterprise cap (free tier: 1,000) — unused |
| `SPOTBOOK_DAILY_CAP_FRACTION` | No | `0.1` | Fraction of monthly cap allowed per UTC day (10% = 800 routes elements/day) |
| `SPOTBOOK_DISCORD_WEBHOOK` | No | — | Personal Discord webhook for operational alerts. Empty = no Discord. Do NOT reuse scan-media webhook. |

---

## Google API billing notes (verified 2026-11-24)

**Billing timezone**: Google Cloud bills on **America/Los_Angeles** (US Pacific Time).
Monthly counters reset at midnight Pacific on the 1st of each month.
Source: confirmed from Google billing documentation.

**Places API (New) SKUs:**
- Essentials: `location`, `displayName`, `formattedAddress`, `id`
- Pro: `timeZone`
- Enterprise: `regularOpeningHours`, `currentOpeningHours`, `rating`, `userRatingCount`, `priceLevel`

We only request Enterprise fields on `save_spot()` and weekly hours refresh.
Text Search (`discover`) uses Essentials fields only.

**Routes API (Compute Route Matrix) SKUs:**
- Essentials: no `routingPreference`, any `travelMode` including TRANSIT
- Pro: `routingPreference: TRAFFIC_AWARE` or `TRAFFIC_AWARE_OPTIMAL`
- Enterprise: two-wheel routing, toll calculation, large vehicle routing

**We never set `routingPreference`** — every `travel_time` call is Essentials.
TRANSIT mode is confirmed Essentials (not listed as a Pro or Enterprise trigger).
`depart_at` is forwarded to the API as a departure time hint for TRANSIT scheduling
but does not trigger traffic routing and does not change the SKU.

Billing unit for the matrix: **1 element = 1 origin × 1 destination**.
One `travel_time` call with 3 destinations = 3 elements.

**Two-layer cost protection:**
1. Code caps (in `api_usage` table, enforced atomically before each call)
2. Google Cloud Console hard quotas (enforced by Google, blocks even if code has a bug)

---

## Google Cloud Console setup (one-time, manual)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create project → name it `spotbook`
3. APIs & Services → Library → enable **Places API (New)** and **Routes API**
4. APIs & Services → Credentials → Create API key
5. Restrict the key:
   - Application restrictions: none (home IP is dynamic — dynamic IPs break key restrictions)
   - API restrictions: **Places API (New)**, **Routes API**
6. Set hard quotas in **Google Maps Platform → Quotas** (last-resort safety net):

| Service | Quota name | Value set |
|---|---|---|
| Routes API | DistanceMatrix - ComputeRouteMatrix per-element quota per day | 500 |
| Routes API | DistanceMatrix - ComputeRouteMatrix per-element quota per minute | 100 |
| Places API (New) | GetPlaceRequest per day | 30 |
| Places API (New) | SearchTextRequest per day | 30 |

7. Store the key as `GOOGLE_MAPS_API_KEY` in `compose/spotbook.env` on media-vm only

---

## GitHub OAuth App setup (one-time, manual)

1. github.com → Settings → Developer settings → OAuth Apps → New OAuth App
2. Application name: `spotbook`
3. Homepage URL: `https://spotbook.xransum.com`
4. Authorization callback URL: `https://spotbook.xransum.com/github/callback`
5. Register → note Client ID and generate Client Secret
6. Store as `SPOTBOOK_GITHUB_CLIENT_ID` and `SPOTBOOK_GITHUB_CLIENT_SECRET`
   in `compose/.env` on media-vm only
7. Find your numeric user ID: `curl https://api.github.com/users/<your-login>`
   → note the `id` field → store as `SPOTBOOK_GITHUB_ALLOWED_USER_ID`

---

## Native install

Not supported for production use — the app requires the MCP SDK and async
runtime. For local development:

```bash
cd docker/spotbook/app
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r ../requirements.txt
# create .env with required vars (see Configuration reference above)
python -m spotbook.server
```

Run unit tests:
```bash
python -m pytest tests/ -v
```

---

## Docker install

See `compose/docker-compose.yml` for the service block. The container exposes
port 8086 and mounts a `/data` volume for the SQLite DB and backups.

```bash
# On media-vm
cd /opt/media/server-stack/compose
docker compose --profile media build spotbook
docker compose --profile media up -d spotbook
docker logs spotbook --tail 50

# Check health
curl http://localhost:8086/health

# Check MCP auth gate
curl -i http://localhost:8086/mcp
# Expect: 401 with WWW-Authenticate header
```

**NPM setup (edge-vm):**
- Proxy host: `spotbook.xransum.com` → `192.168.1.169:8086`
- SSL: existing wildcard cert or new DNS-01 cert
- Custom Nginx (Advanced tab): `proxy_buffering off;`
- Websockets: not required

**Cloudflare Tunnel:**
- Add public hostname: `spotbook.xransum.com` → same NPM backend as other hosts

---

## MCP tools reference

| Tool | Type | Description |
|---|---|---|
| `list_areas` | read | List all configured areas |
| `add_area` | write | Add a new area with center, radius, timezone |
| `update_area` | write | Update area name, center, radius, or timezone |
| `save_spot` | write | Save a place by Google place_id or as a manual spot |
| `list_spots` | read | List saved spots with optional filters (area, occasion, category, open_at, near, distance, rating, price) |
| `check_hours` | read | Check if a spot is open at a given datetime (computed from stored hours, never guessed) |
| `log_visit` | write | Record a visit with rating, notes, occasion, with_whom |
| `discover` | read | Google Text Search biased to area center+radius; returns candidates with already-saved flag |
| `travel_time` | — | Phase 2 — stub only |
