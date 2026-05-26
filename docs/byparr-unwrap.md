# byparr-unwrap

Sidecar HTTP proxy that fixes a known [Byparr](byparr.md) bug where
non-HTML responses (JSON, XML, plain text, PDF) come back wrapped in
Firefox's built-in plaintext-viewer HTML instead of as raw bytes.

- **Port**: 8193 (sits in front of Byparr on 8192)
- **Runs as**: `nobody` (no filesystem or network privileges needed)
- **Implementation**: ~200 lines of stdlib Python, no dependencies
- **Install path**: `/opt/byparr-unwrap/byparr-unwrap.py`

## The bug

Byparr uses [Camoufox](https://camoufox.com/) (a stealth-tuned Firefox)
under Playwright. When Camoufox fetches a URL that returns a non-HTML
content type, Firefox renders the body inside its built-in plaintext
viewer:

```html
<html><head><link rel="stylesheet" href="resource://content-accessible/plaintext.css"></head>
<body><pre>{"id":1,"name":"actual JSON the caller wanted"}</pre></body></html>
```

Byparr returns *that whole HTML page* as `solution.response`, instead of
the raw JSON. Prowlarr (and other FlareSolverr-API consumers) then fail
to parse it. ThePirateBay's `apibay.org` JSON endpoint and any RSS feed
trip this.

Upstream issues, all still open at time of writing:

- [Byparr#353](https://github.com/ThePhaseless/Byparr/issues/353) - Requests from Prowlarr fail, but FastAPI requests succeed
- [Byparr#333](https://github.com/ThePhaseless/Byparr/issues/333) - PDF responds with internal PDF viewer page
- [Byparr#303](https://github.com/ThePhaseless/Byparr/issues/303) - Challenges completed but Prowlarr unable to access

## The fix

`byparr-unwrap` is a transparent HTTP proxy that forwards every request
to Byparr unchanged, and on the way back inspects `solution.response`.
If the body matches the Firefox plaintext-viewer signature, it extracts
the `<pre>` content, HTML-unescapes it, and replaces `solution.response`
with the unwrapped bytes. Everything else (real HTML pages, errors,
non-JSON responses) passes through untouched.

Prowlarr is then pointed at the sidecar instead of at Byparr directly.

This is sidecar-shaped on purpose:

- Zero divergence from upstream Byparr. When Byparr fixes the bug,
  remove the sidecar and re-point Prowlarr at 8192. The repo stays clean.
- Pure stdlib Python; no `pip install` and no virtualenv to maintain.
- Trivially auditable (one file, ~200 lines).

## Install

The script lives in this repo under `scripts/`. Copy it to
`/opt/byparr-unwrap/` and install the systemd unit.

```bash
sudo mkdir -p /opt/byparr-unwrap
sudo cp ~/Documents/Gits/server-stack/scripts/byparr-unwrap.py /opt/byparr-unwrap/byparr-unwrap.py
sudo chmod 0755 /opt/byparr-unwrap/byparr-unwrap.py

sudo cp ~/Documents/Gits/server-stack/services/byparr-unwrap.service /etc/systemd/system/byparr-unwrap.service
sudo systemctl daemon-reload
sudo systemctl enable --now byparr-unwrap
```

Confirm it is listening:

```bash
sudo ss -tlnp | grep 8193
sudo journalctl -u byparr-unwrap -n 20 --no-pager
```

You should see:

```
byparr-unwrap listening on 0.0.0.0:8193, forwarding to http://127.0.0.1:8192 (timeout=300s)
```

## Firewall

The sidecar is localhost-only by default. If you front it from another
host on your LAN:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8193
```

## Smoke test

Hit the sidecar with a request for a known-JSON URL through Byparr.
Before the fix, the response body started with `<html><head>...`.
After the fix, it should start with valid JSON:

```bash
curl -s -X POST http://localhost:8193/v1 \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"request.get","url":"https://apibay.org/precompiled/data_top100_recent.json","maxTimeout":60000}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["solution"]["response"]; print("first 200:", r[:200]); print("valid JSON:", isinstance(json.loads(r), list))'
```

Expected: `first 200: [{"id":...` and `valid JSON: True`.

Cross-check the sidecar log:

```bash
sudo journalctl -u byparr-unwrap -n 5 --no-pager
```

You should see a line like:

```
unwrapped Firefox viewer wrapper for url=https://apibay.org/... (14572 -> 14448 bytes)
```

If you do *not* see the `unwrapped` line, Byparr returned an already-clean
response and the sidecar passed it through (correct behavior for HTML pages).

## Wire Prowlarr to the sidecar

In Prowlarr: Settings -> Indexer Proxies -> edit the Byparr proxy entry
and change the Host from `http://localhost:8192` to `http://localhost:8193`.
Test, then Save.

Indexers tagged `byparr` will now go Prowlarr -> sidecar -> Byparr ->
target site, with the sidecar fixing wrapper-mangled responses on the
return path.

## Configuration

All knobs are environment variables consumed in
`services/byparr-unwrap.service`. Defaults match the in-script defaults:

| Variable             | Default                      | Purpose                                                |
| -------------------- | ---------------------------- | ------------------------------------------------------ |
| `UNWRAP_LISTEN_HOST` | `0.0.0.0`                    | Bind address for the sidecar.                          |
| `UNWRAP_LISTEN_PORT` | `8193`                       | Listen port. Pick anything Byparr is not on.           |
| `UNWRAP_UPSTREAM_URL`| `http://127.0.0.1:8192`      | Where Byparr is actually running.                      |
| `UNWRAP_TIMEOUT`     | `300`                        | Seconds. Cloudflare solves can be slow.                |
| `UNWRAP_LOG_LEVEL`   | `INFO`                       | `DEBUG` will log every pass-through, not just unwraps. |

## Service Management

```bash
sudo systemctl status byparr-unwrap
sudo systemctl restart byparr-unwrap
sudo journalctl -u byparr-unwrap -n 50
sudo journalctl -u byparr-unwrap -f
```

## Removing it (when Byparr fixes the bug upstream)

1. In Prowlarr, change the Byparr proxy Host back to `http://localhost:8192`.
2. `sudo systemctl disable --now byparr-unwrap`
3. `sudo rm /etc/systemd/system/byparr-unwrap.service /opt/byparr-unwrap/byparr-unwrap.py`
4. `sudo rmdir /opt/byparr-unwrap`
5. Delete this doc, `scripts/byparr-unwrap.py`, `services/byparr-unwrap.service`,
   and prune the README entries.

## Troubleshooting

### Prowlarr proxy Test fails with "Unexpected character ... Path '', line 0, position 0."

The sidecar is returning a body Prowlarr cannot parse as JSON. Earlier
versions of this script stripped `Content-Encoding` from upstream
responses without decompressing, so when Prowlarr sent
`Accept-Encoding: gzip`, Byparr's FastAPI gzipped the body and Prowlarr
received raw gzip bytes labeled as plain JSON. Fixed in commit `377b393`.
If you see this on a current build, capture the raw response with
`curl --compressed` against `:8193/v1` and check what came back.

### Prowlarr proxy Test succeeds, but indexer Test reports "Unable to access <site>, blocked by CloudFlare Protection."

Not a sidecar bug. See [Byparr docs - Prowlarr cookie-replay limitation](byparr.md#prowlarr-cookie-replay-limitation).
Prowlarr's FlareSolverr proxy harvests cookies from Byparr then re-requests
the site directly; if the site uses Cloudflare Turnstile, no
`cf_clearance` cookie is issued and the replay always fails. Affects
1337x.to, apibay.org / ThePirateBay, kickass mirrors, and any other
Turnstile-protected indexer regardless of which solver is on the back end.

### Sidecar logs `unwrapped` but Prowlarr still sees garbage

Confirm Byparr is not also behind some other middleware (reverse proxy,
nginx with gzip-on, etc.) that might re-encode after the sidecar. The
sidecar must be the *last* hop before Prowlarr.
