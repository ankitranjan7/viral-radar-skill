# Notification Setup

Load this only when the user needs webhook setup or troubleshooting help.

## Discord

Use Discord incoming webhooks for personal alerts.

1. Create or choose a private server.
2. Create a channel such as `#viral-radar`.
3. Open Server Settings -> Integrations -> Webhooks.
4. Create a webhook for that channel.
5. Copy the webhook URL into `VIRAL_RADAR_DISCORD_WEBHOOK`.
6. Set `VIRAL_RADAR_DESTINATION=discord`.

Discord delivery appends `wait=true` and treats any HTTP `2xx` response as
accepted. The payload disables mentions with `allowed_mentions: {"parse": []}`.

## Slack

Use Slack incoming webhooks when the user already works from Slack.

1. Create a Slack app in the target workspace.
2. Enable Incoming Webhooks.
3. Add a webhook to the desired channel.
4. Copy the webhook URL into `VIRAL_RADAR_SLACK_WEBHOOK`.
5. Set `VIRAL_RADAR_DESTINATION=slack`.

Slack workspace admins may restrict app installation or incoming webhooks.

## Generic Webhook

Advanced users can set:

```dotenv
VIRAL_RADAR_DESTINATION=generic
VIRAL_RADAR_GENERIC_WEBHOOK=https://example.com/webhook
```

The generic payload is JSON:

```json
{
  "text": "post text",
  "url": "https://x.com/user/status/id",
  "result": {
    "id": "tweet id",
    "metrics": {}
  }
}
```

## Safety

- Select exactly one destination in V1.
- Store webhook URLs only in `~/.viral-radar/config.env`.
- Keep `config.env` mode `0600`.
- Do not paste webhook URLs into chats, issues, evidence files, or logs.
- If a webhook leaks, delete or rotate it before testing again.
- Treat HTTP `2xx` as success.
- Retry HTTP `429` and `5xx` once.
- Respect `Retry-After` when the server provides it.
- Use a short safe test payload such as `Viral Radar test alert`.
