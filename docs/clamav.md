# ClamAV

Automatic malware scanning for downloaded media files. ClamAV scans every file after Radarr or Sonarr imports it into the media library.

## Install

```bash
sudo apt install -y clamav clamav-daemon
```

Update the virus database:

```bash
sudo systemctl stop clamav-freshclam
sudo freshclam
sudo systemctl start clamav-freshclam
sudo systemctl enable clamav-daemon
```

`clamav-freshclam` runs as a daemon and keeps the virus database updated automatically.

## Scan Script

Copy the script from this repo and make it executable:

```bash
sudo cp scripts/scan-media.sh /usr/local/bin/scan-media.sh
sudo chmod +x /usr/local/bin/scan-media.sh
```

### How It Works

- `clamscan` exit code `0` = clean, no action taken
- `clamscan` exit code `1` = infected, file is deleted and logged to syslog
- `clamscan` exit code `2+` = scan error (permission denied, corrupt file, etc.), logged but file is kept

Logs are written to syslog and can be viewed with:

```bash
sudo journalctl -t clamav -n 50
```

## Discord Notifications

The scan script can post to a Discord channel when it detects or fails to scan
a file. Notifications are sent via a webhook and require `curl`, which is
installed on Debian by default.

### Setup

Create the config file from the example:

```bash
sudo cp scripts/scan-media.conf.example /etc/scan-media.conf
sudo nano /etc/scan-media.conf
```

Set your webhook URL:

```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<ID>/<TOKEN>
```

To get a webhook URL: Discord server -> Settings -> Integrations -> Webhooks ->
New Webhook -> Copy Webhook URL.

### What Gets Sent

Infected file detected and deleted:

```
:biohazard: Malicious file detected and nuked:
- The Big Lebowski (2014)/The.Big.Lebowski.mkv
```

Scan error (file kept):

```
:warning: ClamAV scan error (exit 2) for:
- The Big Lebowski (2014)/The.Big.Lebowski.mkv
```

If `/etc/scan-media.conf` does not exist or `DISCORD_WEBHOOK_URL` is unset,
notifications are silently skipped and the script behaves as normal.

## Connect To Radarr And Sonarr

In both [Radarr](radarr.md) and [Sonarr](sonarr.md):

- Settings -> Connect -> Add -> Custom Script
  - Name: ClamAV Scan
  - Path: `/usr/local/bin/scan-media.sh`
  - Trigger: On Import
  - Save

Every imported file will be scanned automatically after it lands in your media library.

## Service Management

```bash
sudo systemctl status clamav-daemon
sudo systemctl status clamav-freshclam
sudo systemctl restart clamav-daemon
```

## Manual Scanning

Scan a single file:

```bash
clamscan /path/to/file.mkv
```

Scan an entire directory:

```bash
clamscan -r /mnt/raid/media/Videos/Movies
```

## Notes

- Junk files like `.nfo`, `.jpg`, `.txt`, `.exe` should be excluded at the rdt-client level via its file exclusion regex (see [rdt-client](rdt-client.md#exclude-junk-files)) so they never get downloaded in the first place
- ClamAV's virus database is primarily focused on Windows malware and common threats. It is not a substitute for OS-level security, but it catches the obvious cases
- The `freshclam` daemon updates signatures automatically. No cron job is needed.
