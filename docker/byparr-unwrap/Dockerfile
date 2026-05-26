FROM python:3.13-slim

LABEL org.opencontainers.image.description="byparr-unwrap: sidecar that strips Byparr's Firefox plaintext-viewer wrapper from JSON/XML responses"

WORKDIR /app
# Build context should be repo root: docker build -f docker/byparr-unwrap/Dockerfile .
COPY scripts/byparr-unwrap.py .

USER nobody

ENTRYPOINT ["python", "-u", "/app/byparr-unwrap.py"]
