#!/bin/bash
set -e

echo "[scan-media] starting"

# Update virus definitions on startup (freshclam will then run as daemon for ongoing updates)
echo "[scan-media] updating ClamAV definitions..."
freshclam --quiet || echo "[scan-media] warning: freshclam update failed, using existing definitions"

# Start freshclam daemon for ongoing updates (checks every 2 hours)
echo "[scan-media] starting freshclam daemon..."
freshclam --daemon --quiet &

# Start clamd
echo "[scan-media] starting clamd..."
clamd &

# Wait for clamd socket to be ready (up to 120s)
echo "[scan-media] waiting for clamd socket..."
for i in $(seq 1 60); do
    if [ -S /var/run/clamav/clamd.ctl ]; then
        echo "[scan-media] clamd ready"
        break
    fi
    sleep 2
    if [ "$i" -eq 60 ]; then
        echo "[scan-media] ERROR: clamd socket not ready after 120s, aborting"
        exit 1
    fi
done

echo "[scan-media] starting HTTP server on port 8080..."
exec python3 -u /app/scan-media-server.py
