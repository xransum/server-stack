FROM python:3.13-slim

LABEL org.opencontainers.image.description="torbox-blocklist: auto-blocklist stuck TorBox torrents in Sonarr and Radarr, and clean up completed cloud tasks"

WORKDIR /app
# Build context should be repo root: docker build -f docker/torbox-blocklist/Dockerfile .
COPY scripts/torbox-blocklist.py .

# Runs as nobody: purely REST API calls, no filesystem or Docker socket access.
USER nobody

ENTRYPOINT ["python", "-u", "/app/torbox-blocklist.py"]
