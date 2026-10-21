#!/usr/bin/with-contenv bash
# /custom-cont-init.d/99-calibre-watch
# Launches the books/ready import poller inside the calibre-web container.
# Polls /import (books/ready mount) every 15s — inotify is unreliable over
# NFS when the writer (shelfmark container) is a different NFS client.

WATCH_SCRIPT="/config/calibre-watch.sh"
IMPORT_DIR="/import"
LIBRARY_PATH="/books"
EXTENSIONS="epub|pdf|mobi|cbz|cbr|azw|azw3|djvu|fb2|lit|lrf"
LOG="/config/logs/calibre-watch.log"
FAILED_DIR="/import/failed"

mkdir -p /config/logs "$FAILED_DIR"

cat > "$WATCH_SCRIPT" << 'SCRIPT'
#!/usr/bin/env bash
IMPORT_DIR="/import"
LIBRARY_PATH="/books"
EXTENSIONS="epub|pdf|mobi|cbz|cbr|azw|azw3|djvu|fb2|lit|lrf"
LOG="/config/logs/calibre-watch.log"
FAILED_DIR="/import/failed"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [calibre-watch] $*" | tee -a "$LOG"; }

import_file() {
    local file="$1"
    local filename
    filename="$(basename "$file")"
    local ext="${filename##*.}"

    # Skip non-ebook files and the failed subdir
    if ! echo "$ext" | grep -qiE "^($EXTENSIONS)$"; then
        return
    fi
    if [[ "$file" == "$FAILED_DIR"/* ]]; then
        return
    fi

    log "Importing: $filename"

    # Wait for file size to stabilise (belt-and-suspenders for slow NFS)
    local size1 size2
    size1=$(stat -c%s "$file" 2>/dev/null || echo 0)
    sleep 2
    size2=$(stat -c%s "$file" 2>/dev/null || echo 0)
    if [ "$size1" != "$size2" ]; then
        log "File still growing, skipping: $filename"
        return
    fi

    result=$(calibredb add --library-path "$LIBRARY_PATH" "$file" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ] || echo "$result" | grep -qiE "already exists|duplicate"; then
        log "Imported OK (or already exists): $filename"
        if rm -f "$file"; then
            log "Deleted source: $filename"
        else
            log "WARNING: could not delete source (permission issue?): $filename"
        fi
    else
        log "ERROR importing $filename: $result"
        mkdir -p "$FAILED_DIR"
        mv "$file" "$FAILED_DIR/" 2>/dev/null && log "Moved to failed/: $filename" || true
    fi
}

log "Poller started — monitoring $IMPORT_DIR every 15s"

# Poll loop — runs forever, processes any ebook files found in IMPORT_DIR
while true; do
    find "$IMPORT_DIR" -maxdepth 1 -type f | while read -r file; do
        import_file "$file"
    done
    sleep 15
done
SCRIPT

chmod +x "$WATCH_SCRIPT"

# Kill any stale poller from a previous container start
pkill -f calibre-watch.sh 2>/dev/null || true

nohup bash "$WATCH_SCRIPT" >> "$LOG" 2>&1 &
echo "[calibre-watch] Poller started (PID $!), monitoring $IMPORT_DIR"
