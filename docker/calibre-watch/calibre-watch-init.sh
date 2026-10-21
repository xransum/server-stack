#!/usr/bin/with-contenv bash
# /custom-cont-init.d/99-calibre-watch
# Launches the books/ready import watcher inside the calibre-web container.
# Watches /import (books/ready mount), not /books (the library itself).
# Files are written by shelfmark-post-process.py via atomic rename, so they
# appear complete — no partial-file race. Processes files sequentially.

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

log "Watcher started — monitoring $IMPORT_DIR"

# Process any files already present at startup
find "$IMPORT_DIR" -maxdepth 1 -type f | while read -r existing; do
    import_file "$existing"
done

# Then watch for new arrivals
inotifywait -m -e moved_to -e close_write --format '%w%f' "$IMPORT_DIR" 2>/dev/null | \
while read -r filepath; do
    [ -f "$filepath" ] || continue
    import_file "$filepath"
done
SCRIPT

chmod +x "$WATCH_SCRIPT"

# Ensure inotifywait is available
if ! command -v inotifywait &>/dev/null; then
    echo "[calibre-watch] Installing inotify-tools..."
    apt-get install -y inotify-tools &>/dev/null
fi

# Kill any stale watcher from a previous container start
pkill -f calibre-watch.sh 2>/dev/null || true

nohup bash "$WATCH_SCRIPT" >> "$LOG" 2>&1 &
echo "[calibre-watch] Watcher started (PID $!), monitoring $IMPORT_DIR"
