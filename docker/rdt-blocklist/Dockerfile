FROM python:3.13-slim

LABEL org.opencontainers.image.description="rdt-blocklist: auto-blocklist infringing RDT torrents in Sonarr and Radarr"

WORKDIR /app
# Build context should be repo root: docker build -f docker/rdt-blocklist/Dockerfile .
COPY scripts/rdt-blocklist.py .

# Needs read access to the rdt-client database on the host.
# Run as media (UID 1000) to match the rdt-client service user.
USER nobody

ENTRYPOINT ["python", "-u", "/app/rdt-blocklist.py"]
