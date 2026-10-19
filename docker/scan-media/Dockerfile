FROM debian:12-slim

LABEL org.opencontainers.image.description="scan-media: ClamAV scanning sidecar for Radarr/Sonarr. Scans imported files, deletes infected ones, blocklists the release, and notifies Discord."

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    clamav \
    clamav-daemon \
    clamav-freshclam \
    curl \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Allow freshclam to run as root inside the container
RUN sed -i 's/^#\?DatabaseOwner.*/DatabaseOwner root/' /etc/clamav/freshclam.conf && \
    sed -i 's/^#\?LocalSocket.*/LocalSocket \/var\/run\/clamav\/clamd.ctl/' /etc/clamav/clamd.conf && \
    sed -i 's/^#\?LocalSocketMode.*/LocalSocketMode 666/' /etc/clamav/clamd.conf && \
    sed -i 's/^#\?User.*/User root/' /etc/clamav/clamd.conf && \
    # Update frequency: check every 2 hours (12 checks/day is the default)
    sed -i 's/^#\?Checks.*/Checks 12/' /etc/clamav/freshclam.conf && \
    mkdir -p /var/run/clamav && chmod 755 /var/run/clamav

WORKDIR /app
# Build context is repo root
COPY scripts/scan-media-server.py .
COPY docker/scan-media/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
