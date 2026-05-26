# byparr-proxy

HTTP passthrough that fronts Cloudflare-protected indexers via
[Byparr](byparr.md). Defeats Prowlarr's cookie-replay failure on sites
using Cloudflare Turnstile (1337x, apibay/ThePirateBay, kickass mirrors)
documented in
[Byparr - Prowlarr cookie-replay limitation](byparr.md#prowlarr-cookie-replay-limitation).

- **Ports**: one per upstream indexer, configurable. Defaults: `8881` for 1337x.
- **Runs as**: `nobody` (pure stdlib HTTP server, no filesystem access)
- **Implementation**: vendored from [guyg2232/byparr-proxy](https://github.com/guyg2232/byparr-proxy),
  ~300 lines of stdlib Python, no dependencies
- **Install path**: `/opt/byparr-proxy/byparr-proxy.py`
- **Service**: `byparr-proxy@<instance>.service` template, one instance
  per upstream

## When you need this

Use the byparr-unwrap sidecar alone if your indexers work with
FlareSolverr/Byparr in Prowlarr today. **You only need byparr-proxy for
Turnstile-protected sites where the Prowlarr Test on the indexer
returns** `Unable to access <host>, blocked by CloudFlare Protection.`
even though Byparr's own log shows `status: ok`. That error is the
cookie-replay wall — see [Byparr docs](byparr.md#prowlarr-cookie-replay-limitation)
for the full root-cause analysis.

## How it works

Prowlarr's normal indexer flow (with a FlareSolverr/Byparr proxy
configured) tries the site directly first, then if Cloudflare blocks it,
asks Byparr to solve, then **discards Byparr's response body** and
replays the request directly with the harvested cookies. Turnstile
issues no cookies, so the replay always fails.

byparr-proxy short-circuits the entire flow. Prowlarr is told the
indexer's "Base URL" is `http://127.0.0.1:8881/` (a port on this host),
not `https://1337x.to`. Every request Prowlarr makes goes:

```
Prowlarr -> byparr-proxy (8881) -> Byparr (8192) -> Camoufox -> 1337x
        <-                     <-              <- solved HTML <-
```

Prowlarr only ever sees plain `200 OK` responses with real HTML.
Cloudflare detection inside Prowlarr never fires. There is no replay
because there was no first failed request to retry.

The proxy also short-circuits `/cat/...` paths (used only by indexer
health-tests) with a synthetic results page, and caches successful
responses for an hour with in-flight deduplication so concurrent searches
by Sonarr/Radarr/Prowlarr do not stack on Byparr's serialized browser.

## Install

```bash
sudo mkdir -p /opt/byparr-proxy
sudo cp ~/Documents/Gits/server-stack/scripts/byparr-proxy.py /opt/byparr-proxy/byparr-proxy.py
sudo chmod 0755 /opt/byparr-proxy/byparr-proxy.py

sudo cp ~/Documents/Gits/server-stack/services/byparr-proxy@.service /etc/systemd/system/byparr-proxy@.service
sudo systemctl daemon-reload
```

## Configure an instance (1337x)

Each instance is one upstream site on one port, configured via an env
file under `/etc/byparr-proxy/`. The instance name (after the `@`) must
match the env filename.

```bash
sudo mkdir -p /etc/byparr-proxy
sudo cp ~/Documents/Gits/server-stack/scripts/byparr-proxy.env.example /etc/byparr-proxy/1337x.env
sudo chmod 0644 /etc/byparr-proxy/1337x.env
```

Edit `/etc/byparr-proxy/1337x.env` if you need to change defaults. The
shipped example targets 1337x on port 8881 against the local Byparr at
`127.0.0.1:8192`.

Enable and start:

```bash
sudo systemctl enable --now byparr-proxy@1337x
sudo journalctl -u byparr-proxy@1337x -n 20 --no-pager
```

You should see the startup banner with `upstream: https://1337x.to`,
`byparr: http://127.0.0.1:8192/v1`, and `ready, listening on 0.0.0.0:8881`.

## Drop in the Cardigann definition

Prowlarr loads custom indexer definitions from `Definitions/Custom/`
under its config directory. Native Debian installs put this at
`/var/lib/prowlarr/Definitions/Custom/`.

```bash
sudo mkdir -p /var/lib/prowlarr/Definitions/Custom
sudo cp ~/Documents/Gits/server-stack/definitions/1337x-byparr.yml \
        /var/lib/prowlarr/Definitions/Custom/1337x-byparr.yml
sudo chown prowlarr:prowlarr /var/lib/prowlarr/Definitions/Custom/1337x-byparr.yml
sudo systemctl restart prowlarr
```

The shipped YAML points at `http://127.0.0.1:8881/`. If you changed the
port in the env file, edit `links:` in the YAML to match before
restarting Prowlarr.

**The `Custom/` subdirectory is required** — Prowlarr ignores YAML files
placed directly in `Definitions/`, that path is reserved for its own
mirror of built-in definitions.

## Add the indexer in Prowlarr

1. Prowlarr UI -> **Indexers** -> **+ Add Indexer**.
2. Search for **"1337x (via Byparr)"** in the list and click it.
3. Base URL is already filled in (`http://127.0.0.1:8881/`).
4. **Do not add a FlareSolverr / Byparr proxy tag.** That would
   re-enable Prowlarr's cookie-replay path, which is the thing this
   service exists to bypass.
5. Click **Test**. First test takes 30s-2min while Byparr solves
   Cloudflare for the first time. Should green-check.
6. **Save**.

If the indexer does not appear in the add list, verify Prowlarr can read
the file:

```bash
sudo -u prowlarr ls -l /var/lib/prowlarr/Definitions/Custom/
sudo head -5 /var/lib/prowlarr/Definitions/Custom/1337x-byparr.yml
```

The first line should show `id: 1337x-byparr`.

## Smoke test

Hit the proxy directly with curl. The first call may take 30s-2min while
Byparr solves; subsequent calls within the cache TTL are instant.

```bash
curl -s 'http://127.0.0.1:8881/search/ubuntu/1/' | head -c 200
```

You should see HTML containing 1337x's search results (look for
`<title>` and `/torrent/` links). If the response starts with a
Cloudflare challenge page, Byparr did not solve it — check
`sudo journalctl -u byparr -n 50`.

Check the proxy log for the matching request line:

```bash
sudo journalctl -u byparr-proxy@1337x -n 10 --no-pager
```

You should see something like
`GET /search/ubuntu/1/ from 127.0.0.1 -> forwarding to byparr (...)` and
`<- 200 in 24.11s (byparr 24.09s, 26188 bytes)`.

## Adding more indexers

Each upstream gets its own instance + env file + Cardigann YAML, on a
unique local port. Suggested port allocation: `8881` for 1337x, `8882`
for the next site, and so on, leaving plenty of room before colliding
with anything else in this stack.

For example to add YTS:

1. Create `/etc/byparr-proxy/yts.env` based on the 1337x file:

   ```ini
   UPSTREAM=https://yts.mx
   BYPARR=http://127.0.0.1:8192/v1
   PORT=8882
   TIMEOUT_MS=60000
   LOG_LEVEL=INFO
   CACHE_TTL_S=3600
   STUB_CAT_PATHS=true
   ```

2. Grab the upstream Cardigann YAML for YTS from
   [Prowlarr/Indexers](https://github.com/Prowlarr/Indexers), make two
   edits:
   - Change `id:` to a unique value (e.g. `yts-byparr`)
   - Change `name:` to something distinguishable (e.g. `YTS (via Byparr)`)
   - Replace the `links:` block with `- http://127.0.0.1:8882/`

3. Copy it into `/var/lib/prowlarr/Definitions/Custom/yts-byparr.yml`.

4. `sudo systemctl enable --now byparr-proxy@yts && sudo systemctl restart prowlarr`

5. Add the new indexer in Prowlarr's UI as in the steps above.

## Configuration reference

All knobs are environment variables consumed via `EnvironmentFile=` in
`services/byparr-proxy@.service`:

| Variable          | Default                       | Purpose                                                  |
| ----------------- | ----------------------------- | -------------------------------------------------------- |
| `UPSTREAM`        | `https://1337x.to`            | Indexer base URL the proxy fetches from. No trailing slash. |
| `BYPARR`          | `http://127.0.0.1:8192/v1`    | Byparr's `/v1` endpoint. We deliberately skip the byparr-unwrap sidecar on 8193 — indexer pages are HTML so the wrapper bug does not apply, and the extra hop wastes a Cloudflare solve. |
| `TIMEOUT_MS`      | `120000`                      | Per-request timeout passed to Byparr.                    |
| `PORT`            | `8888`                        | Local listen port. Pick a unique one per instance.       |
| `LOG_LEVEL`       | `INFO`                        | `DEBUG` logs every request including hits and stubs.     |
| `CACHE_TTL_S`     | `3600`                        | In-memory cache TTL for successful responses. Empty result pages are not cached. `0` disables. |
| `STUB_CAT_PATHS`  | `true`                        | Short-circuit `/cat/...` (health-test endpoints) with a synthetic page instead of routing to Byparr. Set `false` if you want Prowlarr to actually browse categories. |

## Why not point at byparr-unwrap (8193) instead of byparr (8192)?

The byparr-unwrap sidecar exists to strip Firefox's plaintext-viewer
HTML from **non-HTML** Byparr responses (JSON, RSS, PDF). The pages
byparr-proxy fetches from indexer sites are always HTML, so the wrapper
bug never fires on this traffic. Going through the sidecar would add a
hop without doing any work. Talk to Byparr directly.

## Service management

```bash
# All byparr-proxy instances at once
sudo systemctl status 'byparr-proxy@*'

# One instance
sudo systemctl restart byparr-proxy@1337x
sudo journalctl -u byparr-proxy@1337x -f
```

## Updating

The script is vendored from [guyg2232/byparr-proxy](https://github.com/guyg2232/byparr-proxy).
To pull upstream changes:

1. Diff `scripts/byparr-proxy.py` in this repo against the upstream
   `byparr-proxy.py` on `main`.
2. Copy the upstream file over `scripts/byparr-proxy.py` (preserve our
   header comment block at the top).
3. `sudo cp scripts/byparr-proxy.py /opt/byparr-proxy/byparr-proxy.py`
4. `sudo systemctl restart 'byparr-proxy@*'`

## Removing it (if Prowlarr eventually fixes the cookie-replay model)

1. In Prowlarr, delete each `(via Byparr)` indexer.
2. Stop and disable each instance:
   `sudo systemctl disable --now byparr-proxy@1337x` (repeat per instance)
3. `sudo rm /etc/systemd/system/byparr-proxy@.service`
4. `sudo rm -r /etc/byparr-proxy /opt/byparr-proxy`
5. `sudo rm /var/lib/prowlarr/Definitions/Custom/1337x-byparr.yml` (etc.)
6. `sudo systemctl daemon-reload && sudo systemctl restart prowlarr`
7. Delete this doc, `scripts/byparr-proxy.py`, `scripts/byparr-proxy.env.example`,
   `services/byparr-proxy@.service`, `definitions/`, and prune the
   README entries.

## Troubleshooting

### Prowlarr indexer Test fails with `byparr request failed: ...`

The proxy could not reach Byparr. Verify:

```bash
curl -s -X POST http://127.0.0.1:8192/v1 \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"sessions.list"}'
```

Should return JSON with `status: ok`. If not, fix Byparr first
(`sudo systemctl status byparr`, `sudo journalctl -u byparr -n 50`).

### Search runs but Prowlarr shows 0 results

The Cardigann YAML's selectors did not match the rendered HTML. The
site may have changed its layout. Pull the latest definition from
[Prowlarr/Indexers](https://github.com/Prowlarr/Indexers), reapply the
two edits (`id` and `links:`), and copy to `Definitions/Custom/`.

You can also look at the raw HTML Byparr returned by checking the proxy
log line for byte count, then re-running the same path with curl and
comparing to the YAML's `fields:` block.

### `Test` succeeds but actual searches return empty

byparr-proxy logs `not cached (empty results, NNN bytes)` for responses
with no `/torrent/` link in the body. If you see that on a search that
should have hits, Byparr returned a stripped or challenge page even
though Cloudflare did not 403 it. Retry — empty pages are deliberately
not cached. If it persists, Byparr is being beaten by the site's CF
config and there is nothing this proxy can do about it.

### Bursts of concurrent searches still time out

Byparr drives a real browser and effectively serializes. The proxy
holds an internal lock around the Byparr call to make this explicit so
N concurrent requests do not all share a single `maxTimeout` window.
If you still see `byparr request failed: timed out`, reduce the load
(stagger Sonarr/Radarr RSS sync intervals, lower interactive search
concurrency).
