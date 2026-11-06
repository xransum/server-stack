FROM python:3.13-slim

LABEL org.opencontainers.image.description="rdt-blocklist: auto-blocklist infringing RDT torrents in Sonarr and Radarr"

WORKDIR /app
# Build context should be repo root: docker build -f docker/rdt-blocklist/Dockerfile .
COPY scripts/rdt-blocklist.py .

# Runs as root to access the Docker socket (/var/run/docker.sock) for
# stopping/starting rdt-client before DB writes to release the WAL lock.

ENTRYPOINT ["python", "-u", "/app/rdt-blocklist.py"]
