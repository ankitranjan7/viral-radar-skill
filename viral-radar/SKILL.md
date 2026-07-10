---
name: viral-radar
description: Install, verify, update, or remove a local cron automation that scans the user's authenticated X For You timeline with Webcmd, detects viral posts, deduplicates deliveries in SQLite, and sends alerts to Discord, Slack, or a generic webhook. Use when the user asks to set up viral X/Twitter post alerts, scheduled viral radar, webhook alerts from their For You timeline, or to troubleshoot/remove this automation.
---

# Viral Radar

Install a user-owned cron job. Scheduled runs execute local Python code only:
they do not invoke Codex, an LLM, or an agent.

## Workflow

1. Explain that setup creates:
   - `~/.viral-radar/viral-timeline.py`
   - `~/.viral-radar/config.env`
   - `~/.viral-radar/state.sqlite3`
   - `~/.viral-radar/output/`
   - `~/.viral-radar/logs/`
2. Check prerequisites:
   - `node --version` must be 20 or newer.
   - `webcmd --version` must work.
   - If Webcmd is absent, ask before running `npm install -g @agentrhq/webcmd`.
3. Refresh bundled Webcmd skills:
   - Run `webcmd skills install --provider codex --scope user`.
   - Load `webcmd-usage` before using live Webcmd commands.
4. Run `webcmd doctor`.
5. Run `webcmd twitter whoami -f json`.
   - If it fails, guide `webcmd twitter login`, then rerun `whoami`.
6. Ask how often to scan. Use `0 * * * *` if the user wants the default hourly schedule.
7. Offer the default detector values and only ask for changes if the user wants them:
   - limit `80`
   - cutoff `6` hours
   - fresh window `2` hours
   - threshold `100`
   - view exponent `0.25`
8. Create `~/.viral-radar/` with `output/` and `logs/`.
9. Copy `scripts/viral-timeline-template.py` to `~/.viral-radar/viral-timeline.py` and make it executable.
10. Ask the webhook question last: Discord, Slack, generic webhook, or help creating one.
    - If help is needed, read `references/notifications.md`.
11. Write `~/.viral-radar/config.env` with mode `0600`.
    - Store exactly one webhook URL.
    - Never print, log, or write webhook URLs to evidence files.
12. Send a safe webhook test alert:
    - Use the same absolute Python path, config, state, output directory, stdout
      redirection, and stderr redirection planned for cron.
    - Add `--test-alert`.
    - Continue only if this exits `0`.
    - Confirm `state.sqlite3` exists and an evidence JSON file was written.
13. Run the generated script once exactly as cron will run it:
    - absolute Python path
    - absolute Webcmd path in `VIRAL_RADAR_WEBCMD`
    - `--config`, `--state`, and `--output-dir`
    - stdout redirected to `logs/viral-radar.log`
    - stderr redirected to `logs/viral-radar.err`
14. Continue only if the normal smoke test exits `0`.
15. Install or replace this managed cron block, preserving unrelated crontab entries:

```cron
# viral-radar begin
<schedule> cd ~/.viral-radar && <python> ~/.viral-radar/viral-timeline.py --config ~/.viral-radar/config.env --state ~/.viral-radar/state.sqlite3 --output-dir ~/.viral-radar/output >> ~/.viral-radar/logs/viral-radar.log 2>> ~/.viral-radar/logs/viral-radar.err
# viral-radar end
```

16. Verify with `crontab -l`.
17. Report:
    - schedule
    - runtime directory
    - config path
    - logs path
    - evidence path
    - removal procedure

## Config Keys

Write these keys in `config.env`:

```dotenv
VIRAL_RADAR_DESTINATION=discord
VIRAL_RADAR_DISCORD_WEBHOOK=...
VIRAL_RADAR_SLACK_WEBHOOK=
VIRAL_RADAR_GENERIC_WEBHOOK=
VIRAL_RADAR_WEBCMD=/absolute/path/to/webcmd
VIRAL_RADAR_LIMIT=80
VIRAL_RADAR_CUTOFF_HOURS=6
VIRAL_RADAR_FRESH_HOURS=2
VIRAL_RADAR_THRESHOLD=100
VIRAL_RADAR_VIEW_EXPONENT=0.25
VIRAL_RADAR_RETENTION_DAYS=30
```

Use only the webhook key matching `VIRAL_RADAR_DESTINATION`.

## Removal

To uninstall, remove only the managed crontab block between `# viral-radar begin`
and `# viral-radar end`. Ask before deleting `~/.viral-radar/`.
