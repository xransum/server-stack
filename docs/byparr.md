# Byparr

Newer Cloudflare bypass proxy for Prowlarr, intended as a replacement for
[FlareSolverr](flaresolverr.md). Byparr exposes the same `/v1` JSON API
FlareSolverr does, so swapping is a one-line URL change in Prowlarr, but
internally it uses [Camoufox](https://camoufox.com/) (a stealth-tuned
Firefox fork driven via Playwright) instead of undetected-chromedriver +
Chromium.

- **Port**: 8192 (chosen so it can run side-by-side with FlareSolverr on
  8191 during A/B testing; flip to 8191 if you decide to fully replace
  FlareSolverr later)
- **Runs as**: your sudo user (needs HOME for Camoufox cache and pyenv path)
- **Install method**: pyenv virtualenv with Python 3.14
- **Virtualenv path**: `~/.pyenv/versions/byparr-env/`
- **Install path**: `/opt/byparr/`

## Why a second Cloudflare bypass

FlareSolverr (see [its doc](flaresolverr.md)) is effectively unmaintained
and its undetected-chromedriver + Chromium backend struggles against
Cloudflare's newer Turnstile CAPTCHAs. Sites like 1337x.to that depend
heavily on Turnstile in 2026 routinely fail to return results through
FlareSolverr on Debian 12's Chromium.

Byparr switches to a Firefox-based stealth stack (Camoufox) which currently
sees materially better Turnstile success rates and is actively developed.
The Python API contract is FlareSolverr-compatible, so Prowlarr does not
care which one is on the other end of the socket.

## Why pyenv (and Python 3.14)

Byparr's `pyproject.toml` pins `requires-python = "==3.14.*"`. Debian 12
ships Python 3.11, so the system interpreter is not usable. We use pyenv to
install Python 3.14 in a dedicated virtualenv, mirroring the existing
FlareSolverr install pattern in this stack.

## Prerequisites

- `pyenv` installed and available for your user
- `pyenv-virtualenv` plugin installed
- System packages: Xvfb and build dependencies for compiling Python from
  source. You likely already have these from the FlareSolverr install;
  `chromium` is no longer required (Camoufox bundles its own Firefox).

## Install System Packages

```bash
sudo apt install -y build-essential xvfb \
    libbz2-dev libffi-dev liblzma-dev libncursesw5-dev libreadline-dev \
    libsqlite3-dev libssl-dev libxml2-dev libxmlsec1-dev tk-dev xz-utils zlib1g-dev
```

These are needed for:

- `xvfb`: virtual framebuffer so Camoufox can run without a display server
- `build-essential` and the `lib*-dev` packages: required for pyenv to
  compile Python from source

## Install Python 3.14 Virtualenv

Run as your normal user (not root):

```bash
# Make sure pyenv knows about Python 3.14
pyenv update

# Install Python 3.14 (substitute the latest 3.14.x available)
pyenv install -s 3.14.0

# Create a dedicated virtualenv
pyenv virtualenv 3.14.0 byparr-env
```

Verify the virtualenv was created:

```bash
ls ~/.pyenv/versions/byparr-env/bin/python
```

## Install Byparr

Byparr is run directly from a checked-out repo (its `main.py` lives at the
repo root and imports from `src/`), so the install is a clone plus a
`pip install .` into the venv to pull dependencies.

```bash
sudo git clone https://github.com/ThePhaseless/Byparr.git /opt/byparr
sudo chown -R $USER:$USER /opt/byparr

~/.pyenv/versions/byparr-env/bin/pip install --upgrade pip
~/.pyenv/versions/byparr-env/bin/pip install /opt/byparr
```

## Pre-warm Camoufox (downloads ~200 MB Firefox)

First run downloads a custom Firefox build (~200 MB). Doing this once
interactively keeps the systemd service's first start from looking like it
hung:

```bash
cd /opt/byparr
~/.pyenv/versions/byparr-env/bin/python main.py --init
```

The `--init` flag exits after the browser is fetched and a health check
succeeds. If you see network errors here, fix them before installing the
service - the same fetch will happen on first start otherwise.

## Systemd Service

Copy the service file from the repo, edit the `YOUR_USER` placeholders,
then enable:

```bash
sudo cp ~/Documents/Gits/server-stack/services/byparr.service /etc/systemd/system/byparr.service
sudo sed -i "s|YOUR_USER|$USER|g" /etc/systemd/system/byparr.service
sudo systemctl daemon-reload
sudo systemctl enable --now byparr
```

Watch the first boot:

```bash
sudo journalctl -u byparr -f
```

You should see Uvicorn announce it is serving on `http://0.0.0.0:8192`.

Key details inside the service file:

- `xvfb-run -a` provides a virtual display for Camoufox. Playwright + Camoufox
  can usually run truly headless; if you confirm yours does, you can drop
  `xvfb-run -a` and shave a process off.
- `HOME` is set so Camoufox finds its Firefox profile/cache directory.
- `PORT=8192` keeps Byparr off FlareSolverr's `8191` so both can run during
  the A/B.
- Full path to the venv Python is used so pyenv shims do not have to load.

## Firewall

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8192
```

## Smoke test

Before touching Prowlarr, hit Byparr directly to confirm the API is up:

```bash
curl -sS -X POST http://localhost:8192/v1 \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"request.get","url":"https://1337x.to","maxTimeout":60000}' \
  | python3 -m json.tool | head -40
```

A healthy response has `"status": "ok"` and `"solution"` populated with a
`url`, `status` 200, and a `response` body containing 1337x HTML. If you see
a Cloudflare challenge page in `response`, Byparr did not solve it.

## Prowlarr integration

Byparr in this stack is **not** wired into Prowlarr as a generic
indexer proxy. The "indexer proxy" mechanism (in both FlareSolverr and
Byparr's case) is the cookie-replay path that
[fails for Turnstile sites](#prowlarr-cookie-replay-limitation), and
none of the currently configured indexers (YTS, Nyaa, Knaben, Torrent
Downloads) need Cloudflare bypass at all.

Byparr's role here is to act as the solving engine behind
[byparr-proxy](byparr-proxy.md), which fronts individual
Turnstile-protected indexers as plain HTTP Base URLs. See that doc for
the actual Prowlarr setup.

If you ever need to put Byparr on Prowlarr's indexer-proxy mechanism
for a classic Cloudflare JS-challenge site (rare in 2026), install the
[byparr-unwrap sidecar](byparr-unwrap.md) first and point Prowlarr at
`http://localhost:8193`. The unwrap sidecar fixes Byparr's
Firefox-plaintext-viewer wrapper bug on JSON/RSS responses; without it
Prowlarr cannot parse the response. The sidecar is shipped disabled by
default in this stack since no indexer currently needs it.

## Service Management

```bash
sudo systemctl status byparr
sudo systemctl restart byparr
sudo journalctl -u byparr -n 50
sudo journalctl -u byparr -f
```

## Updating

```bash
sudo systemctl stop byparr
sudo git -C /opt/byparr pull
~/.pyenv/versions/byparr-env/bin/pip install --upgrade /opt/byparr
sudo systemctl start byparr
```

If a Byparr update bumps the Python version pin (it requires `==3.14.*`
today, may change), `pip install` will fail noisily. In that case install
the new Python, recreate the venv, and reinstall:

```bash
pyenv install -s 3.15.0
pyenv uninstall -f byparr-env
pyenv virtualenv 3.15.0 byparr-env
~/.pyenv/versions/byparr-env/bin/pip install /opt/byparr
sudo systemctl restart byparr
```

## Troubleshooting

### `requires-python` install error

If `pip install /opt/byparr` errors with something like
`Requires Python ==3.14.*; the running Python is X.Y`, you are pointing pip
at the wrong interpreter. Always use the absolute path
`~/.pyenv/versions/byparr-env/bin/pip`, never `pip` from your shell.

### Camoufox / Firefox download fails

The first run pulls a custom Firefox build from GitHub releases. If your
network blocks GitHub or rate-limits, the download will time out. Re-run
`python main.py --init` after fixing the network issue; the partial cache
under `~/.cache/camoufox` may need to be deleted first.

### Service starts but `/v1` requests return 500 or hang

Run the `--init` step manually as the same user. Most "request hangs"
failures trace back to Camoufox failing to launch (missing Xvfb, missing
HOME, Firefox cache permission problem).

### Port collision with FlareSolverr

If both services try to bind 8191 you will see `Address already in use` in
journald. Confirm Byparr is on `PORT=8192`:

```bash
sudo systemctl show byparr -p Environment
sudo ss -tlnp | grep -E '8191|8192'
```

### Same indexer fails on both FlareSolverr and Byparr

That is a Cloudflare-side win, not a Byparr bug. Per the FlareSolverr doc's
*Realistic Expectations* section, no open-source bypass tool is universal.
Fall back to indexers that do not require Cloudflare bypass for that
content type.

## Prowlarr cookie-replay limitation

Verified behavior of Prowlarr 2.x against Cloudflare Turnstile sites
(1337x, apibay/ThePirateBay, kickass mirrors): even when Byparr
successfully solves the challenge, Prowlarr still reports
`Unable to access <host>, blocked by CloudFlare Protection.`

This is **architectural in Prowlarr**, not a Byparr or sidecar bug.
Reading `NzbDrone.Core.IndexerProxies.FlareSolverr.FlareSolverr.PostResponse`:

1. Prowlarr requests the indexer URL **directly** (Byparr not involved).
2. If the response has `Server: cloudflare` AND status `503`/`403` AND
   the body title looks like a CF challenge, Prowlarr decides CF is
   blocking it and routes through Byparr.
3. Byparr solves the challenge inside Camoufox and returns the page
   plus a `solution.cookies` array.
4. Prowlarr **discards `solution.response`**, copies cookies and
   user-agent onto a new request, and re-fetches the URL **directly**
   with its own HttpClient.
5. If that re-request still trips CF detection, the
   "blocked by CloudFlare Protection" error is raised.

For sites using the classic Cloudflare JS challenge, the harvested
`cf_clearance` cookie is sufficient and the replay succeeds. For sites
using **Turnstile** (which is what 1337x/apibay/etc. have moved to in
2026), Cloudflare does not issue a `cf_clearance` cookie at all -
Turnstile validates per-request via a one-shot token, so
`solution.cookies` from Byparr is empty and the replay fails instantly.

Confirm with:

```bash
# Direct call returns 403 with cf-mitigated: challenge.
curl -sI -A "Mozilla/5.0" https://apibay.org/precompiled/data_top100_recent.json | grep -iE 'http|server|cf-'

# Byparr solves it (status 200) but solution.cookies is [].
curl -s -X POST http://localhost:8193/v1 \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"request.get","url":"https://apibay.org/precompiled/data_top100_recent.json","maxTimeout":60000}' \
  | python3 -c 'import json,sys; s=json.load(sys.stdin)["solution"]; print("status:",s["status"]); print("cookies:",[c["name"] for c in s["cookies"]])'
```

The empty cookie list is the smoking gun.

### What does and does not work

| Indexer type                              | Status through Byparr  |
| ----------------------------------------- | ---------------------- |
| No Cloudflare (Nyaa, etc.)                | Works                  |
| Classic CF JS challenge (issues clearance)| Works                  |
| Cloudflare Turnstile (1337x, apibay, ...) | **Fails** (no cookies) |

There is no workaround at the Byparr or sidecar layer. The fix is to
take Prowlarr out of the cookie-replay path entirely by fronting the
target site as a plain HTTP indexer Base URL — that is what
[byparr-proxy](byparr-proxy.md) does. Prowlarr only ever sees clean
`200 OK` HTML from `127.0.0.1`, never triggers its CF detection, never
attempts a replay. Use it for any Turnstile-protected indexer you want
to keep.

A "real" fix in Prowlarr would be to use Byparr's `solution.response`
body directly instead of replaying. That is an upstream Prowlarr change
nobody has shipped.

Until then: install [byparr-proxy](byparr-proxy.md) per Turnstile site,
or substitute equivalent indexers that do not require bypass.

---

## Docker install

In the Docker Compose lab target, Byparr runs as a container in the
`cloudflare-bypass` profile using the official
`ghcr.io/thephaseless/byparr` image. No custom Dockerfile needed.

```bash
docker compose -f compose/docker-compose.yml --profile cloudflare-bypass up -d byparr
```

Inside the Docker network, Byparr is reachable at `http://byparr:8191/v1`.
byparr-proxy instances and byparr-unwrap point their `BYPARR=` env var
at this address (not `localhost`) — this is already set correctly in
`compose/docker-compose.yml`.

Byparr requires a real browser (Camoufox/Firefox) and significant RAM
(~500MB–1GB per active solve). No special Docker flags needed beyond
what the official image handles internally.
