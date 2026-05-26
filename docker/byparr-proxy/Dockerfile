FROM python:3.13-slim

LABEL org.opencontainers.image.description="byparr-proxy: HTTP passthrough fronting Cloudflare-Turnstile indexers via Byparr"

WORKDIR /app
# Build context should be repo root: docker build -f docker/byparr-proxy/Dockerfile .
COPY scripts/byparr-proxy.py .

# Run as nobody — stateless HTTP proxy, no filesystem access needed
USER nobody

ENTRYPOINT ["python", "-u", "/app/byparr-proxy.py"]
