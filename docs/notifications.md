# Discord Notifications

Set up Discord webhook notifications for Radarr and Sonarr to get alerts when new content is downloaded or upgraded.

## Discord Webhook Setup

1. In your Discord server, go to the channel you want notifications in
2. Edit Channel -> Integrations -> Webhooks -> New Webhook
3. Name it (e.g. `Radarr` or `Sonarr`) and copy the webhook URL

You can use separate channels for movies and TV (e.g. `#movie-updates` and `#tv-updates`) or a shared channel (e.g. `#media-updates`).

## Radarr

1. Settings -> Connect -> + -> Discord
2. Paste your webhook URL
3. Recommended triggers:
   - **On Import** - notifies when a new movie is downloaded and imported
   - **On Upgrade** - notifies when a better quality version replaces an existing file
4. Test and Save

## Sonarr

1. Settings -> Connect -> + -> Discord
2. Paste your webhook URL
3. Recommended triggers:
   - **On Import** - notifies when a new episode is downloaded and imported
   - **On Upgrade** - notifies when a better quality version replaces an existing file
4. Test and Save

## Bulk Import Warning

If you are about to bulk import an existing media library into Radarr or Sonarr, **disable the Discord connection first**. Otherwise you will get a flood of notifications for every file imported. After the import is complete, re-enable the connection.

To disable temporarily:

1. Settings -> Connect -> click the Discord entry
2. Uncheck the **Enabled** toggle
3. Save
4. Do your bulk import
5. Re-enable and Save
