# Flatten Downloads Service

Automatic flattening of single-file download folders created by rdt-client.

## The Problem

rdt-client creates a folder for each download and puts the file inside it. When a torrent contains a single mkv file, this results in a nested structure:

```text
Downloads/radarr/Movie.Name.mkv/   <- folder
    Movie.Name.mkv                 <- actual file
```

Radarr and Sonarr cannot import from this structure because they expect the file directly in the category folder, not nested inside a subfolder with the same name.

## The Solution

The `flatten-downloads` service watches both download directories using `inotifywait` and automatically flattens single-file mkv folders once the download is complete.

Multi-file downloads (season packs, movies with subtitles) are left untouched since they contain more than one file.

## How It Works

1. rdt-client creates a folder in the radarr or sonarr download directory
2. `inotifywait` detects the new folder instantly
3. The script checks if the folder name ends in `.mkv` and contains exactly one `.mkv` file
4. It polls the file size every 10 seconds until it stops changing (download complete)
5. Once complete, it moves the mkv out of the folder, removes the folder, and fixes ownership to `rdtclient:mediadl` with `664` permissions
6. Radarr/Sonarr can now import cleanly

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

    if [[ "$dir" != *.mkv ]]; then
        return
    fi

    local count
    count=$(find "$dir" -maxdepth 1 -name "*.mkv" | wc -l)

    if [ "$count" -ne 1 ]; then
        return
    fi

    local file
    file=$(find "$dir" -maxdepth 1 -name "*.mkv")

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

    chown rdtclient:mediadl "$parent/$base"
    chmod 664 "$parent/$base"

    echo "Flattened and fixed permissions: $parent/$base"
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
User=root
ExecStart=/usr/local/bin/flatten-downloads.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flatten-downloads
```

The service runs as root because it needs to change file ownership after flattening.

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
for dir in *.mkv; do
    if [ -d "$dir" ]; then
        sudo mv "$dir/$dir" "${dir}.tmp"
        sudo rm -rf "$dir"
        sudo mv "${dir}.tmp" "$dir"
    fi
done

for dir in */; do
    if [ -d "$dir" ]; then
        sudo find "$dir" -name "*.mkv" -exec sudo mv {} . \;
        sudo rm -rf "$dir"
    fi
done
```

Repeat for `/mnt/raid/media/Downloads/radarr` if needed.

## Limitations

- Only handles folders whose name ends in `.mkv`
- Only flattens folders containing exactly one `.mkv` file
- Multi-file downloads (season packs, extras) are ignored
- Non-mkv formats (`.mp4`, `.avi`) in nested folders are not handled
- The 10-second polling interval means there is a brief delay after download completion before the file is flattened
