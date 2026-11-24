# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# tzdata is required by zoneinfo for IANA timezone support
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Build stage — install Python dependencies
# ---------------------------------------------------------------------------
FROM base AS builder

WORKDIR /build
COPY docker/spotbook/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM base AS runtime

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
WORKDIR /app
COPY docker/spotbook/app/ .

# Create data directory — volume will be mounted here at runtime.
# We stay as root so the volume mount is writable regardless of how Docker
# initialises the named volume. spotbook has no Docker socket and no need
# to touch other system resources, so root is acceptable here.
RUN mkdir -p /data /data/backups

EXPOSE 8086

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8086/health', timeout=4)"

# uvicorn flags:
#   --proxy-headers: trust X-Forwarded-* from NPM (required for correct redirects)
#   --forwarded-allow-ips=*: NPM is the only thing that can reach this container
CMD ["python", "-m", "uvicorn", "spotbook.server:app", \
     "--host", "0.0.0.0", "--port", "8086", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
