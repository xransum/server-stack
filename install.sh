#!/bin/bash

# Media Stack Installer
# Installs: Radarr, Sonarr, Prowlarr, rdt-client, FlareSolverr
# Tested on Debian 12

set -euo pipefail

# CONFIG - edit these before running

MEDIA_ROOT="/mnt/raid/media"
MOVIES_DIR="$MEDIA_ROOT/Videos/Movies"
TV_DIR="$MEDIA_ROOT/Videos/TV Shows"
DOWNLOADS_DIR="$MEDIA_ROOT/Downloads"
MEDIA_GROUP="plex"
LAN_CIDR="192.168.1.0/24"

RADARR_VERSION="5.21.1.9799"
SONARR_VERSION="4.0.17.2953"
FLARESOLVERR_PYTHON_VERSION="3.11.9"
FLARESOLVERR_ENV_NAME="flaresolverr-env"

RADARR_URL="https://github.com/Radarr/Radarr/releases/download/v${RADARR_VERSION}/Radarr.master.${RADARR_VERSION}.linux-core-x64.tar.gz"
SONARR_URL="https://github.com/Sonarr/Sonarr/releases/download/v${SONARR_VERSION}/Sonarr.develop.${SONARR_VERSION}.linux-x64.tar.gz"
PROWLARR_URL="https://github.com/Prowlarr/Prowlarr/releases/latest/download/Prowlarr.master.linux-core-x64.tar.gz"
RDTCLIENT_URL="https://github.com/rogerfar/rdt-client/releases/latest/download/RealDebridClient.zip"

# HELPERS

info() { echo -e "\e[34m[INFO]\e[0m $1"; }
ok()   { echo -e "\e[32m[OK]\e[0m $1"; }
warn() { echo -e "\e[33m[WARN]\e[0m $1"; }
die()  { echo -e "\e[31m[ERROR]\e[0m $1"; exit 1; }

require_root() {
    [ "$EUID" -eq 0 ] || die "Run this script with sudo"
}

require_group() {
    getent group "$MEDIA_GROUP" >/dev/null || die "Group '$MEDIA_GROUP' does not exist"
    groupadd mediadl 2>/dev/null || true
}

require_sudo_user() {
    [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] || die "Run this script with sudo from your normal user account"
}

create_service_user() {
    local user="$1"
    if id "$user" >/dev/null 2>&1; then
        warn "User $user already exists"
    else
        useradd -r -s /usr/sbin/nologin "$user"
    fi
}

install_archive() {
    local url="$1"
    local archive="$2"
    local target_dir="$3"
    local owner="$4"

    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    curl -fsSL "$url" -o "$archive"
    tar -xzf "$archive" -C "$target_dir" --strip-components=1
    chown -R "$owner:$owner" "$target_dir"
    rm -f "$archive"
}

allow_lan_port() {
    local port="$1"
    ufw allow from "$LAN_CIDR" to any port "$port" 2>/dev/null || true
}

# SYSTEM DEPS

install_deps() {
    info "Installing system dependencies..."
    apt update -q
    apt install -y curl wget unzip mediainfo sqlite3

    if ! dpkg -s aspnetcore-runtime-10.0 >/dev/null 2>&1; then
        info "Installing ASP.NET Core runtime 10.0..."
        wget -q https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O /tmp/ms-prod.deb
        dpkg -i /tmp/ms-prod.deb
        apt update -q
        apt install -y aspnetcore-runtime-10.0
        rm -f /tmp/ms-prod.deb
    else
        ok "ASP.NET Core runtime 10.0 already installed"
    fi
}

# MEDIA DIRECTORIES

setup_dirs() {
    info "Setting up media directories..."
    mkdir -p "$MOVIES_DIR" "$TV_DIR" "$DOWNLOADS_DIR"
    mkdir -p "$DOWNLOADS_DIR/radarr" "$DOWNLOADS_DIR/sonarr"

    chgrp -R "$MEDIA_GROUP" "$MEDIA_ROOT/Videos"
    chmod 2775 "$MEDIA_ROOT/Videos" "$MOVIES_DIR" "$TV_DIR"

    ok "Directories ready"
}

# RADARR

install_radarr() {
    info "Installing Radarr $RADARR_VERSION..."

    create_service_user radarr
    usermod -aG "$MEDIA_GROUP" radarr

    install_archive "$RADARR_URL" "/tmp/radarr.tar.gz" "/opt/Radarr" radarr

    mkdir -p /var/lib/radarr
    chown radarr:radarr /var/lib/radarr

    install -m 0644 /dev/stdin /etc/systemd/system/radarr.service <<'EOF'
[Unit]
Description=Radarr
After=network.target

[Service]
User=radarr
Group=mediadl
UMask=0002
ExecStart=/opt/Radarr/Radarr -nobrowser -data=/var/lib/radarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now radarr

    allow_lan_port 7878

    ok "Radarr installed - http://localhost:7878"
}

# SONARR

install_sonarr() {
    info "Installing Sonarr $SONARR_VERSION..."

    create_service_user sonarr
    usermod -aG "$MEDIA_GROUP" sonarr

    install_archive "$SONARR_URL" "/tmp/sonarr.tar.gz" "/opt/Sonarr" sonarr

    mkdir -p /var/lib/sonarr
    chown sonarr:sonarr /var/lib/sonarr

    install -m 0644 /dev/stdin /etc/systemd/system/sonarr.service <<'EOF'
[Unit]
Description=Sonarr
After=network.target

[Service]
User=sonarr
Group=mediadl
UMask=0002
ExecStart=/opt/Sonarr/Sonarr -nobrowser -data=/var/lib/sonarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now sonarr

    allow_lan_port 8989

    ok "Sonarr installed - http://localhost:8989"
}

# PROWLARR

install_prowlarr() {
    info "Installing Prowlarr..."

    create_service_user prowlarr

    install_archive "$PROWLARR_URL" "/tmp/prowlarr.tar.gz" "/opt/Prowlarr" prowlarr

    mkdir -p /var/lib/prowlarr
    chown prowlarr:prowlarr /var/lib/prowlarr

    install -m 0644 /dev/stdin /etc/systemd/system/prowlarr.service <<'EOF'
[Unit]
Description=Prowlarr
After=network.target

[Service]
User=prowlarr
Group=mediadl
UMask=0002
ExecStart=/opt/Prowlarr/Prowlarr -nobrowser -data=/var/lib/prowlarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now prowlarr

    allow_lan_port 9696

    ok "Prowlarr installed - http://localhost:9696"
}

# RDT-CLIENT

install_rdtclient() {
    info "Installing rdt-client..."

    create_service_user rdtclient

    rm -rf /opt/rdt-client
    mkdir -p /opt/rdt-client
    curl -fsSL "$RDTCLIENT_URL" -o /tmp/rdt-client.zip
    unzip -q /tmp/rdt-client.zip -d /opt/rdt-client
    chown -R rdtclient:rdtclient /opt/rdt-client
    rm -f /tmp/rdt-client.zip

    # Share the downloads path between rdt-client and the *arr services.
    chown -R rdtclient:mediadl "$DOWNLOADS_DIR"
    chmod -R g+rw "$DOWNLOADS_DIR"
    chmod 2775 "$DOWNLOADS_DIR" "$DOWNLOADS_DIR/radarr" "$DOWNLOADS_DIR/sonarr"

    install -m 0644 /dev/stdin /opt/rdt-client/appsettings.json <<EOF
{
  "Logging": {
    "File": {
      "Path": "${DOWNLOADS_DIR}/rdtclient.log",
      "FileSizeLimitBytes": 5242880,
      "MaxRollingFiles": 5
    }
  },
  "Database": {
    "Path": "${DOWNLOADS_DIR}/rdtclient.db"
  },
  "Port": "6500",
  "BasePath": null
}
EOF
    chown rdtclient:rdtclient /opt/rdt-client/appsettings.json

    install -m 0644 /dev/stdin /etc/systemd/system/rdt-client.service <<'EOF'
[Unit]
Description=rdt-client
After=network.target

[Service]
User=rdtclient
Group=mediadl
UMask=0002
WorkingDirectory=/opt/rdt-client
ExecStart=/usr/bin/dotnet /opt/rdt-client/RdtClient.Web.dll --urls=http://0.0.0.0:6500
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now rdt-client

    allow_lan_port 6500

    ok "rdt-client installed - http://localhost:6500"
}

# FLARESOLVERR

install_flaresolverr() {
    local flare_user="$SUDO_USER"
    local flare_home
    local pyenv_root
    local python_bin

    info "Installing FlareSolverr via pyenv + Python $FLARESOLVERR_PYTHON_VERSION..."

    apt install -y build-essential chromium chromium-driver xvfb libbz2-dev libffi-dev liblzma-dev libncursesw5-dev libreadline-dev libsqlite3-dev libssl-dev libxml2-dev libxmlsec1-dev tk-dev xz-utils zlib1g-dev

    flare_home="$(getent passwd "$flare_user" | cut -d: -f6)"
    [ -n "$flare_home" ] || die "Could not determine home directory for $flare_user"

    pyenv_root="$flare_home/.pyenv"
    python_bin="$pyenv_root/versions/$FLARESOLVERR_ENV_NAME/bin/python"

    [ -x "$pyenv_root/bin/pyenv" ] || die "pyenv not found at $pyenv_root - install pyenv first then re-run"

    sudo -u "$flare_user" env PYENV_ROOT="$pyenv_root" PATH="$pyenv_root/bin:$PATH" bash -lc '
        eval "$(pyenv init -)"
        pyenv virtualenv --help >/dev/null 2>&1
    ' || die "pyenv-virtualenv is required for FlareSolverr"

    sudo -u "$flare_user" env PYENV_ROOT="$pyenv_root" PATH="$pyenv_root/bin:$PATH" bash -lc "
        eval \"\$(pyenv init -)\"
        eval \"\$(pyenv virtualenv-init -)\"
        pyenv install -s $FLARESOLVERR_PYTHON_VERSION
        pyenv virtualenv $FLARESOLVERR_PYTHON_VERSION $FLARESOLVERR_ENV_NAME 2>/dev/null || true
        pyenv activate $FLARESOLVERR_ENV_NAME
        python -m pip install -q --upgrade pip flaresolverr
    "

    [ -x "$python_bin" ] || die "FlareSolverr virtualenv was not created successfully"

    install -m 0644 /dev/stdin /etc/systemd/system/flaresolverr.service <<EOF
[Unit]
Description=FlareSolverr
After=network.target

[Service]
User=$flare_user
WorkingDirectory=$flare_home
ExecStart=/usr/bin/xvfb-run -a $python_bin -m flaresolverr
Restart=on-failure
Environment=HOME=$flare_home
Environment=LOG_LEVEL=info
Environment=PORT=8191

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now flaresolverr

    allow_lan_port 8191

    ok "FlareSolverr installed - http://localhost:8191"
}

# CLAMAV

install_clamav() {
    info "Installing ClamAV..."

    apt install -y clamav clamav-daemon
    systemctl stop clamav-freshclam || true
    freshclam
    systemctl enable --now clamav-freshclam
    systemctl enable --now clamav-daemon

    install -m 0755 /dev/stdin /usr/local/bin/scan-media.sh <<'EOF'
#!/bin/bash

file="$1"

if [ -z "$file" ] || [ ! -e "$file" ]; then
    exit 0
fi

clamscan --no-summary --quiet "$file"
status=$?

if [ "$status" -eq 1 ]; then
    logger -t clamav "INFECTED FILE DETECTED: $file"
    rm -f "$file"
elif [ "$status" -gt 1 ]; then
    logger -t clamav "ClamAV scan failed for $file with exit code $status"
fi
EOF

    ok "ClamAV installed - add /usr/local/bin/scan-media.sh as a Custom Script in Radarr and Sonarr (On Import trigger)"
}

# FLATTEN DOWNLOADS

install_flatten() {
    info "Installing flatten-downloads service..."

    apt install -y inotify-tools

    install -m 0755 /dev/stdin /usr/local/bin/flatten-downloads.sh <<'EOF'
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
EOF

    install -m 0644 /dev/stdin /etc/systemd/system/flatten-downloads.service <<EOF
[Unit]
Description=Flatten single-file mkv download folders
After=network.target

[Service]
User=$SUDO_USER
Group=mediadl
UMask=0002
ExecStart=/usr/local/bin/flatten-downloads.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now flatten-downloads

    ok "flatten-downloads service installed and running"
}

# GROUP PERMISSIONS

setup_groups() {
    info "Setting up shared media group permissions..."

    for user in rdtclient radarr sonarr prowlarr plex tautulli "$SUDO_USER"; do
        usermod -aG mediadl "$user" 2>/dev/null || warn "Could not add $user to mediadl (user may not exist)"
    done

    # plex group for media library access
    usermod -aG "$MEDIA_GROUP" radarr 2>/dev/null || true
    usermod -aG "$MEDIA_GROUP" sonarr 2>/dev/null || true
    usermod -aG "$MEDIA_GROUP" rdtclient 2>/dev/null || true

    chown -R rdtclient:mediadl "$DOWNLOADS_DIR"
    chmod -R g+rw "$DOWNLOADS_DIR"
    chmod g+s "$DOWNLOADS_DIR/radarr"
    chmod g+s "$DOWNLOADS_DIR/sonarr"

    systemctl restart radarr sonarr rdt-client prowlarr

    ok "Group permissions set"
}

# SUMMARY

print_summary() {
    echo ""
    echo "================================================"
    echo " Install complete. Manual steps required:"
    echo "================================================"
    echo ""
    echo " 1. rdt-client (http://localhost:6500)"
    echo "    - Create login on first visit"
    echo "    - Settings -> Provider -> paste RD API key"
    echo "      from https://real-debrid.com/apitoken"
    echo "    - Settings -> Download Client -> set path to:"
    echo "      $DOWNLOADS_DIR"
    echo ""
    echo " 2. Prowlarr (http://localhost:9696)"
    echo "    - Add indexers: YTS, TorrentsCSV, Torrent Downloads"
    echo "    - Settings -> Apps -> add Radarr + Sonarr"
    echo ""
    echo " 3. Radarr (http://localhost:7878)"
    echo "    - Settings -> Download Clients -> add qBittorrent"
    echo "      Host: localhost, Port: 6500"
    echo "    - Settings -> Media Management -> Root Folders:"
    echo "      $MOVIES_DIR"
    echo ""
    echo " 4. Sonarr (http://localhost:8989)"
    echo "    - Same download client as Radarr"
    echo "    - Root folder: $TV_DIR"
    echo ""
    echo " 5. Overseerr (http://localhost:5055)"
    echo "    - Settings -> Services -> add Radarr + Sonarr"
    echo ""
    echo " 6. FlareSolverr (http://localhost:8191)"
    echo "    - In Prowlarr: Settings -> Indexer Proxies"
    echo "      -> add FlareSolverr at http://localhost:8191"
    echo "    - Assign to Cloudflare-protected indexers"
    echo ""
    echo " 7. ClamAV"
    echo "    - In Radarr + Sonarr: Settings -> Connect"
    echo "      -> Custom Script -> /usr/local/bin/scan-media.sh"
    echo "      -> Trigger: On Import"
    echo ""
    echo "================================================"
}

# MAIN

require_root
require_group
require_sudo_user
install_deps
setup_dirs
install_radarr
install_sonarr
install_prowlarr
install_rdtclient
install_flaresolverr
install_clamav
install_flatten
setup_groups
print_summary
