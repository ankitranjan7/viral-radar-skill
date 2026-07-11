---
name: viral-radar
description: Set up, check, tune, troubleshoot, or remove Viral Radar - a local cron automation that scans the user's authenticated X (Twitter) For You timeline with Webcmd, scores posts for viral velocity, deduplicates deliveries in SQLite, and sends alerts to a Discord, Slack, or generic webhook. Use whenever the user wants alerts or notifications about viral or trending posts from their X/Twitter timeline, scheduled or recurring scanning of their feed, or mentions viral-radar at all - including asking whether it is running, changing its schedule or thresholds, debugging missing or duplicate alerts, or uninstalling it. Also use for requests like "ping me when something blows up on my timeline" that do not name the skill explicitly.
---

# Viral Radar

Viral Radar installs a user-owned cron job on this machine. Everything under
`~/.viral-radar/` belongs to the user, and the scheduled runs execute plain
local Python only — they never invoke an LLM, an agent, or this skill. Your job
is to set that automation up correctly (or inspect, tune, and remove it), then
get out of the way.

Files the automation uses:

| Path | Purpose |
|------|---------|
| `~/.viral-radar/viral-timeline.py` | The scan script (copied from `scripts/viral-timeline-template.py`) |
| `~/.viral-radar/config.env` | Settings + webhook URL (mode `0600`) |
| `~/.viral-radar/state.sqlite3` | Delivery log used for deduplication |
| `~/.viral-radar/output/` | One JSON evidence file per run |
| `~/.viral-radar/logs/` | `viral-radar.log` (stdout) and `viral-radar.err` (stderr) |

Route by what the user needs:

- **Set it up** → [Install](#install)
- **"Is it working?"** → [Verify](#verify)
- **Change schedule, thresholds, or destination** → [Update](#update)
- **Errors, missed alerts, duplicates, cron not firing** → read `references/troubleshooting.md`
- **Uninstall** → [Removal](#removal)

## How detection works

Understanding the detector lets you explain alerts and tune thresholds with
the user instead of treating the numbers as magic.

Each run fetches up to `VIRAL_RADAR_LIMIT` posts from the For You timeline and
keeps those newer than `VIRAL_RADAR_CUTOFF_HOURS`. Each post gets an
engagement score weighted toward signals that indicate genuine conversation:

```
engagement = likes + 3*retweets + 9*replies + 7*quotes + 5*bookmarks
normalized_score = engagement / views ^ VIRAL_RADAR_VIEW_EXPONENT
```

Dividing by `views^exponent` rewards posts whose engagement is high *relative
to reach* — a fast-moving post from a small account outranks a celebrity post
with passive views. A post alerts when its normalized score exceeds
`VIRAL_RADAR_THRESHOLD`. Posts younger than `VIRAL_RADAR_FRESH_HOURS` are all
eligible; if none qualify, only the single newest older qualifier is sent, so
a backlog never floods the webhook.

A boost filter drops likely-astroturfed posts: suspicious amplification shape
(retweets+quotes far outpacing replies, or quotes outpacing retweets) from an
author under 20k followers.

Deduplication is by tweet ID in `state.sqlite3` — a post alerts once, ever,
until rows older than `VIRAL_RADAR_RETENTION_DAYS` are pruned.

## Install

### 1. Confirm the plan

Tell the user what setup creates (the file table above), that it requires a
Webcmd browser session logged into X, and how often it will run. Get their
go-ahead before touching their machine.

### 2. Check prerequisites

The scan script shells out to Webcmd, which needs Node:

- `node --version` — must be 20 or newer.
- `webcmd --version` — must succeed. If Webcmd is missing, ask before running
  `npm install -g @agentrhq/webcmd` (it is a global install on their machine).
- If your harness supports Webcmd's bundled skills (e.g. on Codex:
  `webcmd skills install --provider codex`), refresh them and load
  `webcmd-usage` before issuing live Webcmd commands. Otherwise rely on
  `webcmd --help`.

### 3. Verify X authentication

Run `webcmd doctor`, then `webcmd twitter whoami --window background -f json`.

If `whoami` fails, walk the user through `webcmd twitter login` in a visible
browser window, then rerun `whoami --window background`. Use `background` for
everything scripted — it prevents the browser from stealing focus during
scheduled runs — but note that browser-backed X commands still require the
Webcmd/Cloak browser runtime to be installed and logged in. The scan script
already handles one stale-browser-context failure per run by restarting the
Webcmd daemon and retrying.

### 4. Gather settings

- **Schedule**: ask how often to scan; default is hourly (`0 * * * *`).
  Mention that the machine must be awake for cron to fire.
- **Detector values**: offer the defaults (see [Config keys](#config-keys))
  and only discuss individual knobs if the user wants changes.
- **Webhook destination** (ask last, since it may require the user to leave
  and create one): Discord, Slack, or generic webhook. If they need help
  creating one, read `references/notifications.md`.

### 5. Create the runtime files

1. Create `~/.viral-radar/` with `output/` and `logs/`.
2. Copy `scripts/viral-timeline-template.py` to
   `~/.viral-radar/viral-timeline.py` and make it executable.
3. Write `~/.viral-radar/config.env` with mode `0600`, containing the keys
   from [Config keys](#config-keys) and exactly one webhook URL (the one
   matching `VIRAL_RADAR_DESTINATION`).

The webhook URL is a credential: anyone holding it can post to the user's
channel. Never print it, log it, or let it end up in evidence files, shell
history you echo back, or chat output. If it leaks, tell the user to rotate it.

#### Securely collect the webhook on macOS

Do not ask the user to paste the webhook into chat, and do not read its value
back after collecting it. On macOS, open a native hidden-input dialog and
redirect the value directly to a protected temporary file:

```bash
umask 077
osascript -e 'text returned of (display dialog "Paste your webhook URL:" default answer "" with hidden answer buttons {"Cancel", "Save"} default button "Save" cancel button "Cancel")' > ~/.viral-radar/.webhook.tmp
```

If the user cancels, `osascript` exits non-zero — stop and ask how they want
to proceed. Otherwise sanity-check the file without printing its contents
(e.g. `grep -qE '^https://' ~/.viral-radar/.webhook.tmp`), then splice it into
`config.env` and delete the temp file in commands that never echo the value:

```bash
printf 'VIRAL_RADAR_DISCORD_WEBHOOK=%s\n' "$(cat ~/.viral-radar/.webhook.tmp)" >> ~/.viral-radar/config.env
rm ~/.viral-radar/.webhook.tmp
chmod 600 ~/.viral-radar/config.env
```

(Substitute the key matching `VIRAL_RADAR_DESTINATION`.) On other platforms,
or if the dialog cannot be shown, have the user open `config.env` in their own
editor and paste the URL there themselves — never route it through the
conversation.

### 6. Test exactly as cron will run

Cron runs with a minimal environment — no shell profile, near-empty `PATH`,
different working directory. Most "worked when I set it up, silent ever since"
failures come from testing under interactive conditions. So both test runs
must use:

- an absolute Python path
- an absolute Webcmd path in `VIRAL_RADAR_WEBCMD` (from `which webcmd`)
- `VIRAL_RADAR_WEBCMD_WINDOW=background`
- explicit `--config`, `--state`, and `--output-dir`
- stdout appended to `logs/viral-radar.log`, stderr to `logs/viral-radar.err`
- a stripped `PATH` matching cron's, not your interactive shell's — Webcmd's
  binary starts with `#!/usr/bin/env node`, so it needs `node` resolvable on
  `PATH` even though you already gave it an absolute path in
  `VIRAL_RADAR_WEBCMD`. Find `node`'s directory with `which node` and confirm
  it's on the `PATH` you're about to put in the crontab (step 7) — an absolute
  `VIRAL_RADAR_WEBCMD` alone is not enough. Run at least the `--test-alert`
  pass wrapped in `env -i PATH=<the exact PATH you plan to put in cron>
  HOME="$HOME" ...` so a missing `node` surfaces now instead of showing up
  as `env: node: No such file or directory` in `viral-radar.err` after the
  first real cron fire.

First run with `--test-alert` added: it sends a harmless "Viral Radar test
alert" to the webhook. Continue only if it exits `0`, `state.sqlite3` exists,
and an evidence JSON appeared in `output/`. Then run once without
`--test-alert` for a full live scan and require exit `0` again.

### 7. Install the cron entry

Install or replace this managed block, preserving all unrelated crontab
entries (the markers exist so update and removal can never clobber the user's
other jobs). Set `PATH` explicitly to whatever you verified in step 6 —
cron's default `PATH` is minimal and won't include the directory holding
`node`, which Webcmd needs to even start:

```cron
# viral-radar begin
PATH=<dir containing node>:/usr/bin:/bin:/usr/sbin:/sbin
<schedule> cd ~/.viral-radar && <python> ~/.viral-radar/viral-timeline.py --config ~/.viral-radar/config.env --state ~/.viral-radar/state.sqlite3 --output-dir ~/.viral-radar/output >> ~/.viral-radar/logs/viral-radar.log 2>> ~/.viral-radar/logs/viral-radar.err
# viral-radar end
```

Verify with `crontab -l`, then report to the user: the schedule, the runtime
directory, where config/logs/evidence live, and how removal works.

## Verify

When the user asks whether Viral Radar is working:

1. `crontab -l` — confirm the managed block exists and read its schedule.
2. Check the newest file in `~/.viral-radar/output/` — its timestamped name
   tells you when the last successful run finished, and its `selected` /
   `delivered` fields tell you what it found.
3. Tail `~/.viral-radar/logs/viral-radar.err` for recent failures.
4. If freshness or errors look wrong, or the user reports missed/duplicate
   alerts, continue in `references/troubleshooting.md`.

An empty `selected` array is not a failure — it means nothing on the timeline
crossed the threshold that run.

## Update

- **Detector values or destination**: edit `~/.viral-radar/config.env` (keep
  mode `0600`). The script rereads it every run, so no cron change is needed.
  After changing the destination or webhook, rerun the `--test-alert` command
  from Install step 6.
- **Schedule**: rewrite only the managed cron block with the new schedule,
  preserving everything outside the markers. Verify with `crontab -l`.
- **Script**: if this skill ships a newer template, re-copy it over
  `~/.viral-radar/viral-timeline.py` and rerun both Install step 6 tests.

## Config keys

Write these keys in `config.env`. Only the webhook key matching
`VIRAL_RADAR_DESTINATION` should have a value.

```dotenv
VIRAL_RADAR_DESTINATION=discord
VIRAL_RADAR_DISCORD_WEBHOOK=...
VIRAL_RADAR_SLACK_WEBHOOK=
VIRAL_RADAR_GENERIC_WEBHOOK=
VIRAL_RADAR_WEBCMD=/absolute/path/to/webcmd
VIRAL_RADAR_WEBCMD_WINDOW=background
VIRAL_RADAR_LIMIT=80
VIRAL_RADAR_CUTOFF_HOURS=6
VIRAL_RADAR_FRESH_HOURS=2
VIRAL_RADAR_THRESHOLD=100
VIRAL_RADAR_VIEW_EXPONENT=0.25
VIRAL_RADAR_RETENTION_DAYS=30
```

| Key | Default | Meaning |
|-----|---------|---------|
| `LIMIT` | 80 | Timeline posts fetched per scan |
| `CUTOFF_HOURS` | 6 | Ignore posts older than this |
| `FRESH_HOURS` | 2 | Posts younger than this all compete; older qualifiers send one at most |
| `THRESHOLD` | 100 | Minimum normalized score to alert — raise for fewer alerts, lower for more |
| `VIEW_EXPONENT` | 0.25 | View normalization strength — higher favors low-view fast movers |
| `RETENTION_DAYS` | 30 | Prune dedup rows and evidence files after this many days |

## Removal

1. Remove only the managed crontab block between `# viral-radar begin` and
   `# viral-radar end`, leaving every other entry intact. Verify with
   `crontab -l`.
2. Ask before deleting `~/.viral-radar/` — it holds the user's delivery
   history and their webhook URL. If they keep the directory, remind them
   `config.env` still contains the webhook credential.
