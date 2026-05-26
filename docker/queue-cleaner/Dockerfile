FROM python:3.13-slim

LABEL org.opencontainers.image.description="queue-cleaner: removes stalled rdt-client queue items from Sonarr/Radarr"

WORKDIR /app
# Build context should be repo root: docker build -f docker/queue-cleaner/Dockerfile .
COPY scripts/queue-cleaner.py .

# Needs no filesystem access — API calls only
USER nobody

ENTRYPOINT ["python", "-u", "/app/queue-cleaner.py"]
