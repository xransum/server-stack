#!/bin/bash

# /usr/local/bin/scan-media.sh
# Mounted into Radarr and Sonarr containers as a Custom Script (On Import trigger).
# POSTs file path + metadata to the scan-media sidecar container which runs
# ClamAV, deletes infected files, blocklists the release, and notifies Discord.
#
# Native systemd (legacy): this script used to call clamscan directly.
# Docker: delegates all logic to scan-media:8080/scan over HTTP.
#
# Environment variables injected by Radarr on import:
#   radarr_eventtype, radarr_moviefile_path, radarr_download_id, radarr_movie_id
#
# Environment variables injected by Sonarr on import:
#   sonarr_eventtype, sonarr_episodefile_path, sonarr_download_id, sonarr_series_id

SCAN_MEDIA_URL="http://scan-media:8080/scan"

# Determine which service triggered this and extract the relevant vars
if [ -n "$radarr_moviefile_path" ]; then
    FILE="$radarr_moviefile_path"
    DOWNLOAD_ID="${radarr_download_id:-}"
    SERVICE="radarr"
    MEDIA_ID="${radarr_movie_id:-}"
elif [ -n "$sonarr_episodefile_path" ]; then
    FILE="$sonarr_episodefile_path"
    DOWNLOAD_ID="${sonarr_download_id:-}"
    SERVICE="sonarr"
    MEDIA_ID="${sonarr_series_id:-}"
else
    # Not an import event (e.g. test notification) — nothing to do
    exit 0
fi

if [ -z "$FILE" ] || [ ! -e "$FILE" ]; then
    exit 0
fi

# POST to scan-media container and capture HTTP response code
PAYLOAD="{\"path\":\"${FILE}\",\"download_id\":\"${DOWNLOAD_ID}\",\"service\":\"${SERVICE}\",\"media_id\":\"${MEDIA_ID}\"}"

HTTP_CODE=$(wget -qO- \
    --server-response \
    --post-data="$PAYLOAD" \
    --header="Content-Type: application/json" \
    "$SCAN_MEDIA_URL" 2>&1 | grep "HTTP/" | tail -1 | awk '{print $2}')

# If scan-media returned 5xx (infected), exit non-zero so Radarr/Sonarr mark it failed
if [ -n "$HTTP_CODE" ] && [ "$HTTP_CODE" -ge 500 ] 2>/dev/null; then
    exit 1
fi

exit 0
