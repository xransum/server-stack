# Notifications

Sonarr and Radarr both support Discord webhook notifications natively.

## Discord setup

1. In your Discord server, open the channel you want notifications in
2. Go to channel Settings > Integrations > Webhooks > New Webhook
3. Copy the webhook URL
4. In Sonarr or Radarr go to Settings > Connect > + > Discord
5. Paste the webhook URL and save

## Recommended triggers

### Sonarr
- On Import - fires when a new episode is downloaded and added to the library
- On Upgrade - fires when a better quality version replaces an existing file
- On Health Issue - fires when something breaks in Sonarr

### Radarr
- On Import - fires when a new movie is downloaded and added to the library
- On Upgrade - fires when a better quality version replaces an existing file
- On Health Issue - fires when something breaks in Radarr

## Notes

- Disable the connection before bulk importing a library or you will get one
  notification per episode imported
- Sonarr and Radarr can each point to different channels if you want to separate
  TV and movie notifications
- Suggested channel names: #tv-updates and #movie-updates, or a shared #media-updates
- On Grab fires too early (download queued but not finished) - not recommended
