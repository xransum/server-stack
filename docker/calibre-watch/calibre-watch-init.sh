#!/usr/bin/with-contenv bash
# /etc/cont-init.d/99-calibre-watch
# Launches the Calibre library watcher as a background process.
# Runs inside the calibre-web container via linuxserver custom-cont-init.d.

WATCH_SCRIPT="/config/calibre-watch.sh"
LIBRARY_PATH="/books"
WATCH_DIR="/books"
EXTENSIONS="epub|pdf|mobi|cbz|cbr|azw|azw3|djvu|fb2"
DEBOUNCE=5
LOG="/config/logs/calibre-watch.log"

mkdir -p /config/logs

# Write the watcher script to /config so it survives image updates
cat > "$WATCH_SCRIPT" << 'SCRIPT'
#!/usr/bin/env bash
LIBRARY_PATH="/books"
WATCH_DIR="/books"
EXTENSIONS="epub|pdf|mobi|cbz|cbr|azw|azw3|djvu|fb2"
DEBOUNCE=5
LOG="/config/logs/calibre-watch.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [calibre-watch] $*" | tee -a "$LOG"; }

log "Watcher started — monitoring $WATCH_DIR"

pending=0
last_event=0

import() {
    log "New ebook detected, importing to Calibre library..."
    result=$(calibredb add "$LIBRARY_PATH" --library-path "$LIBRARY_PATH" --recurse 2>&1)
    if echo "$result" | grep -q "Added book ids"; then
        ids=$(echo "$result" | grep "Added book ids" | grep -oE '[0-9, ]+')
        log "Imported book IDs: $ids"
    else
        log "No new books to import"
    fi
    pending=0
}

inotifywait -m -r -e close_write -e moved_to --format '%f' "$WATCH_DIR" 2>/dev/null | \
while read -r filename; do
    ext="${filename##*.}"
    if echo "$ext" | grep -qiE "^($EXTENSIONS)$"; then
        log "New file: $filename"
        pending=1
        last_event=$(date +%s)
    fi
    if [ "$pending" -eq 1 ]; then
        now=$(date +%s)
        if [ $((now - last_event)) -ge $DEBOUNCE ]; then
            import
        fi
    fi
done
SCRIPT

chmod +x "$WATCH_SCRIPT"

# Check inotifywait is available
if ! command -v inotifywait &>/dev/null; then
    echo "[calibre-watch] inotifywait not found, installing inotify-tools..."
    apt-get install -y inotify-tools &>/dev/null
fi

# Kill any existing watcher
pkill -f calibre-watch.sh 2>/dev/null || true

# Launch watcher in background
nohup bash "$WATCH_SCRIPT" >> "$LOG" 2>&1 &
echo "[calibre-watch] Watcher started (PID $!)"
