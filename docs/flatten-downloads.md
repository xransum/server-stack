# Flatten Downloads Service

Automatic flattening of single-file download folders created by rdt-client.

## The Problem

rdt-client creates a folder for each download and puts the file inside it. When a torrent contains a single media file, this results in a nested structure:

```text
Downloads/radarr/Movie.Name.mkv/   <- folder
    Movie.Name.mkv                 <- actual file
```

Radarr and Sonarr cannot import from this structure because they expect the file directly in the category folder, not nested inside a subfolder with the same name.

## The Solution

The `flatten-downloads` service watches both download directories using `inotifywait` and automatically flattens single-file `.mkv` and `.mp4` folders once the download is complete.

Multi-file downloads (season packs, movies with subtitles) are left untouched since they contain more than one file.

## How It Works

1. rdt-client creates a folder in the radarr or sonarr download directory
2. `inotifywait` detects the new folder instantly
3. The script checks if the folder name ends in `.mkv` or `.mp4`
4. It waits for rdt-client to finish writing the `.download` temp file and rename it to the final filename (up to 10 minutes)
5. It polls the file size every 10 seconds until it stops changing (download complete)
6. Once complete, it moves the file out of the folder and removes the folder
7. Radarr/Sonarr can now import cleanly

Permissions are handled by the service umask (`0002`) and group (`mediadl`), so no `chown`/`chmod` is needed after flattening.

## Prerequisites

```bash
sudo apt install -y inotify-tools
```

## Install

Create `/usr/local/bin/flatten-downloads.sh`:

```bash
#!/bin/bash

WATCH_DIRS=(
    "/mnt/raid/media/Downloads/radarr"
    "/mnt/raid/media/Downloads/sonarr"
)

flatten() {
    local dir="$1"

    if [[ "$dir" != *.mkv ]] && [[ "$dir" != *.mp4 ]]; then
        return
    fi

    # Wait for the actual media file (not .download temp file)
    local file
    local attempts=0
    while true; do
        file=$(find "$dir" -maxdepth 1 \( -name "*.mkv" -o -name "*.mp4" \) ! -name "*.download" 2>/dev/null | head -1)
        if [ -n "$file" ]; then
            break
        fi
        attempts=$((attempts + 1))
        if [ "$attempts" -gt 60 ]; then
            echo "Timed out waiting for completed file in $dir"
            return
        fi
        sleep 10
    done

    # Wait until file size stops changing (download complete)
    local prev_size=-1
    local curr_size
    while true; do
        curr_size=$(stat -c%s "$file" 2>/dev/null || echo 0)
        if [ "$curr_size" -eq "$prev_size" ] && [ "$curr_size" -gt 0 ]; then
            break
        fi
        prev_size=$curr_size
        sleep 10
    done

    local parent
    parent=$(dirname "$dir")
    local base
    base=$(basename "$file")

    mv "$file" "$parent/${base}.tmp"
    rm -rf "$dir"
    mv "$parent/${base}.tmp" "$parent/$base"

    echo "Flattened: $parent/$base"
}

export -f flatten

for WATCH_DIR in "${WATCH_DIRS[@]}"; do
    inotifywait -m -e create -e moved_to --format '%w%f' "$WATCH_DIR" | while read path; do
        if [ -d "$path" ]; then
            flatten "$path" &
        fi
    done &
done

wait
```

Make it executable:

```bash
sudo chmod +x /usr/local/bin/flatten-downloads.sh
```

## Systemd Service

Create `/etc/systemd/system/flatten-downloads.service`:

```ini
[Unit]
Description=Flatten single-file mkv download folders
After=network.target

[Service]
User=kevin
Group=mediadl
UMask=0002
ExecStart=/usr/local/bin/flatten-downloads.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Replace `kevin` with your username if different.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flatten-downloads
```

## Service Management

```bash
sudo systemctl status flatten-downloads
sudo systemctl restart flatten-downloads
sudo journalctl -u flatten-downloads -n 50
```

## Manual Flatten

If the service was not running when downloads completed, or you need to fix existing nested folders:

```bash
cd /mnt/raid/media/Downloads/sonarr
for dir in *.mkv *.mp4; do
    if [ -d "$dir" ]; then
        sudo mv "$dir/$dir" "${dir}.tmp"
        sudo rm -rf "$dir"
        sudo mv "${dir}.tmp" "$dir"
    fi
done

for dir in */; do
    if [ -d "$dir" ]; then
        sudo find "$dir" \( -name "*.mkv" -o -name "*.mp4" \) -exec sudo mv {} . \;
        sudo rm -rf "$dir"
    fi
done
```

Repeat for `/mnt/raid/media/Downloads/radarr` if needed.

## Limitations

- Only handles folders whose name ends in `.mkv` or `.mp4`
- Only flattens folders containing a single media file
- Multi-file downloads (season packs, extras) are ignored
- Other formats (`.avi`, `.ts`) in nested folders are not handled
- The `.download` temp file wait has a 10-minute timeout - stalled downloads are skipped
- The 10-second polling interval means there is a brief delay after download completion before the file is flattened
