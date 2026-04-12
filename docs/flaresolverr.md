# FlareSolverr

Cloudflare bypass proxy for Prowlarr. FlareSolverr uses a headless Chromium browser to solve Cloudflare challenges so Prowlarr can access protected indexer sites.

- **Port**: 8191
- **Runs as**: your sudo user (needs access to pyenv)
- **Install method**: pyenv virtualenv with Python 3.11
- **Virtualenv path**: `~/.pyenv/versions/flaresolverr-env/`

## Why pyenv

FlareSolverr cannot be installed through the normal channels on Debian 12:

- **Prebuilt binary**: requires glibc 2.38+, but Debian 12 (Bookworm) ships glibc 2.36. The binary fails with `GLIBC_2.38 not found`.
- **pip install with system Python**: Debian 12's default Python is 3.11, but if you have upgraded to Python 3.13, the `cgi` module was removed in that version and FlareSolverr depends on it.
- **Solution**: use pyenv to install Python 3.11.9 in a dedicated virtualenv, which avoids both problems.

## Prerequisites

- `pyenv` installed and available for your user
- `pyenv-virtualenv` plugin installed
- System packages: Chromium, Xvfb, and build dependencies for compiling Python

## Install System Packages

```bash
sudo apt install -y build-essential chromium chromium-driver xvfb \
    libbz2-dev libffi-dev liblzma-dev libncursesw5-dev libreadline-dev \
    libsqlite3-dev libssl-dev libxml2-dev libxmlsec1-dev tk-dev xz-utils zlib1g-dev
```

These are needed for:
- `chromium` / `chromium-driver`: the headless browser FlareSolverr controls
- `xvfb`: virtual framebuffer so Chromium can run without a display server
- `build-essential` and the `lib*-dev` packages: required for pyenv to compile Python from source

## Install Python 3.11 Virtualenv

Run as your normal user (not root):

```bash
# Install Python 3.11.9
pyenv install -s 3.11.9

# Create a dedicated virtualenv
pyenv virtualenv 3.11.9 flaresolverr-env

# Activate it and install FlareSolverr
pyenv activate flaresolverr-env
python -m pip install --upgrade pip flaresolverr
```

Verify the virtualenv was created:

```bash
ls ~/.pyenv/versions/flaresolverr-env/bin/python
```

## Systemd Service

Create `/etc/systemd/system/flaresolverr.service`, replacing `YOUR_USER` and `/home/YOUR_USER` with your actual username and home directory:

```ini
[Unit]
Description=FlareSolverr
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER
ExecStart=/usr/bin/xvfb-run -a /home/YOUR_USER/.pyenv/versions/flaresolverr-env/bin/python -m flaresolverr
Restart=on-failure
Environment=HOME=/home/YOUR_USER
Environment=LOG_LEVEL=info
Environment=PORT=8191

[Install]
WantedBy=multi-user.target
```

Key details:
- `xvfb-run -a` provides a virtual display for Chromium
- `HOME` must be set so FlareSolverr can find Chromium profile directories
- The full path to the virtualenv Python is used instead of relying on pyenv shims

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flaresolverr
```

## Firewall

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8191
```

## Prowlarr Integration

In Prowlarr:

- Settings -> Indexer Proxies -> Add -> FlareSolverr
  - Host: `http://localhost:8191`
  - Test and Save
- When adding a Cloudflare-protected indexer, assign the FlareSolverr tag in the Tags field

## Service Management

```bash
sudo systemctl status flaresolverr
sudo systemctl restart flaresolverr
sudo journalctl -u flaresolverr -n 50
```

## Chromium Compatibility

FlareSolverr's effectiveness depends entirely on the Chromium version available on your system. Cloudflare continuously updates its challenge mechanisms, and older Chromium versions may not be able to solve them.

### Debian 12 Chromium Versions

| Source | Chromium Version | Notes |
|---|---|---|
| Debian 12 stable | ~131 | Ships with the release, updated infrequently |
| Debian 12 backports | varies | May have a slightly newer version |
| Debian testing/unstable | ~136+ | More current but mixing repos can cause issues |

### Cloudflare Challenge Types

- **JS Challenge**: the original challenge type. Most Chromium versions handle this.
- **Managed Challenge**: newer, more aggressive. Chromium 131+ can often solve it.
- **Turnstile CAPTCHA**: deployed since late 2025 on heavily protected sites. Chromium 131-136 has limited success. Sites like 1337x and TorrentGalaxy increasingly use this.

### What To Do If FlareSolverr Fails

1. Check logs for specific errors:
   ```bash
   sudo journalctl -u flaresolverr -n 100
   ```
2. Verify Chromium is installed and the correct version:
   ```bash
   chromium --version
   ```
3. Try upgrading Chromium from Debian backports or testing (with caution):
   ```bash
   # Add backports if not already configured
   echo 'deb http://deb.debian.org/debian bookworm-backports main' | sudo tee /etc/apt/sources.list.d/backports.list
   sudo apt update
   sudo apt install -t bookworm-backports chromium
   ```
4. For sites where FlareSolverr consistently fails, use indexers that do not require Cloudflare bypass:
   - **YTS** - no Cloudflare, excellent for movies
   - **TorrentsCSV** - no Cloudflare, general purpose
   - **Private trackers** - no Cloudflare, most reliable long-term

### Realistic Expectations

As of early 2026, FlareSolverr is useful for some Cloudflare-protected sites but is not a universal solution. Cloudflare's protections are getting more sophisticated faster than open-source bypass tools can keep up. For a reliable stack, build your indexer list around sites that do not need FlareSolverr and treat it as a bonus for the ones where it works.

## Troubleshooting

### Service Starts But Returns Errors

Check that the `WorkingDirectory` in the service file points to an existing directory. If it points to a directory that was deleted, FlareSolverr will return HTTP 200 but fail internally with a CHDIR error.

### Chromium Crashes On Start

Ensure `xvfb` is installed and the service uses `xvfb-run`:

```bash
sudo apt install -y xvfb
```

The `ExecStart` line should begin with `/usr/bin/xvfb-run -a`.

### pyenv Not Found

The service runs as your user but does not load your shell profile. That is why the service file uses the full path to the virtualenv Python (`~/.pyenv/versions/flaresolverr-env/bin/python`) rather than relying on pyenv shims or `pyenv activate`.
