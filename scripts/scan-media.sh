#!/bin/bash

# /usr/local/bin/scan-media.sh
# ClamAV scan script called by Radarr/Sonarr on import.
# Deletes infected files and optionally sends Discord notifications.

file="$1"

if [ -z "$file" ] || [ ! -e "$file" ]; then
    exit 0
fi

# Load Discord webhook URL if config exists
DISCORD_WEBHOOK_URL=""
if [ -f /etc/scan-media.conf ]; then
    # shellcheck source=/dev/null
    source /etc/scan-media.conf
fi

# Send a Discord notification if a webhook URL is configured
notify_discord() {
    local message="$1"
    if [ -z "$DISCORD_WEBHOOK_URL" ]; then
        return
    fi
    curl -s -o /dev/null \
        -H "Content-Type: application/json" \
        -d "{\"content\": \"$message\"}" \
        "$DISCORD_WEBHOOK_URL"
}

# Extract a short display name from the full path for notifications
display_name="$(basename "$(dirname "$file")")/$(basename "$file")"

clamscan --no-summary --quiet "$file"
status=$?

if [ "$status" -eq 1 ]; then
    logger -t clamav "INFECTED FILE DETECTED: $file"
    rm -f "$file"
    notify_discord ":biohazard: Malicious file detected and nuked:\n- $display_name"
elif [ "$status" -gt 1 ]; then
    logger -t clamav "ClamAV scan failed for $file with exit code $status"
    notify_discord ":warning: ClamAV scan error (exit $status) for:\n- $display_name"
fi
