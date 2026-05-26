# DEPRECATED: dns-updater is superseded by Cloudflare Tunnel (cloudflared).
# If the cloudflared service (proxy profile) is running, this service is not needed.
# Kept for reference and fallback only.
#
# Note: dns-updater uses the requests and python-dotenv packages (not stdlib-only)
# because it predates the stdlib-only convention. It also uses DreamHost's DNS API,
# which may not be your DNS provider in the lab. Review dns-updater/dns_updater.py
# and docs/dns-updater.md before using this in Docker.

FROM python:3.13-slim

WORKDIR /app
# Build context should be repo root: docker build -f docker/dns-updater/Dockerfile .
COPY dns-updater/dns_updater.py .

RUN pip install --no-cache-dir requests python-dotenv

# dns-updater reads env vars for credentials — no filesystem access needed
USER nobody

ENTRYPOINT ["python", "-u", "/app/dns_updater.py"]
