# DNS Updater

A dynamic DNS updater for DreamHost. Runs as a systemd oneshot service on a
timer, checks the server's current public IPv4 and IPv6 addresses, and updates
any stale A or AAAA records via the DreamHost API. Sends Discord webhook
notifications on changes or errors.

## How it works

1. Fetches the current public IPv4 and IPv6 from `ipify.org`
2. Pulls all DNS records from the DreamHost API
3. For each record in `TARGET_RECORDS`:
   - If the record is missing, it is added
   - If the record value does not match the current IP, the old record is
     removed and the new one is added
4. Posts a Discord notification summarizing any changes, or alerts on failure

## Prerequisites

- Debian 12 (Bookworm)
- Python 3.11+
- pip
- A DreamHost account with API access enabled
- A Discord server with an incoming webhook configured (optional, but required
  for notifications)

## Setup

### 1. Copy the script to its install location

```bash
sudo mkdir -p /opt/dns-updater
sudo cp dns-updater/dns_updater.py /opt/dns-updater/
sudo cp dns-updater/requirements.txt /opt/dns-updater/
```

### 2. Create a virtual environment and install dependencies

```bash
cd /opt/dns-updater
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Create the .env file

```bash
sudo cp dns-updater/.env.example /opt/dns-updater/.env
sudo nano /opt/dns-updater/.env
```

Fill in the values:

| Variable | Description |
| --- | --- |
| `DREAMHOST_API_TOKEN` | Your DreamHost API key. Generate one at https://panel.dreamhost.com/?tree=home.api |
| `DISCORD_WEBHOOK_ID` | The numeric ID from your Discord webhook URL |
| `DISCORD_WEBHOOK_TOKEN` | The token from your Discord webhook URL |
| `TARGET_RECORDS` | Comma-separated list of records to manage in `record:type` format |

Example `TARGET_RECORDS` value:

```
TARGET_RECORDS=home.example.com:A,home.example.com:AAAA
```

### 4. Set ownership and permissions

Replace `<your-username>` with the user the service will run as.

```bash
sudo chown -R <your-username>:<your-username> /opt/dns-updater
sudo chmod 600 /opt/dns-updater/.env
```

## Installation

### 1. Copy the service and timer files

```bash
sudo cp services/dns-updater.service /etc/systemd/system/
sudo cp services/dns-updater.timer /etc/systemd/system/
```

### 2. Edit the service file

Open `/etc/systemd/system/dns-updater.service` and replace `<your-username>`
with the user the service should run as.

### 3. Enable and start the timer

```bash
sudo systemctl daemon-reload
sudo systemctl enable dns-updater.timer
sudo systemctl start dns-updater.timer
```

The timer will trigger the updater 5 minutes after boot and then every hour.

## Service management

```bash
# Check timer status
sudo systemctl status dns-updater.timer

# Check last service run
sudo systemctl status dns-updater.service

# Run the updater manually (outside the timer)
sudo systemctl start dns-updater.service

# View logs
sudo journalctl -u dns-updater.service -n 50

# Disable the timer
sudo systemctl disable dns-updater.timer
sudo systemctl stop dns-updater.timer
```

## File structure

```
/opt/dns-updater/
    dns_updater.py      <- main script
    requirements.txt    <- pip dependencies
    .env                <- runtime config (not committed)
    venv/               <- Python virtual environment
```
