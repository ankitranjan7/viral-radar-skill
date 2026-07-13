# Troubleshooting

Load this when an installed Viral Radar misbehaves: cron not firing, webcmd
errors, webhook failures, missed or duplicate alerts.

## Look at the evidence first

Three sources tell you almost everything:

1. `~/.viral-radar/logs/viral-radar.err` — every failed run prints one
   `viral-radar: <reason>` line here.
2. `~/.viral-radar/output/` — one timestamped JSON per completed scan
   (written even when deliveries fail). The newest filename is the last
   completed run time; `selected`, `delivered`, and `failures` show what that
   run decided.
3. `crontab -l` — the managed block between `# viral-radar begin` and
   `# viral-radar end` shows the schedule and the exact command.

To reproduce a failure interactively, run the cron command manually **without**
the log redirections so errors print to the terminal, e.g.:

```bash
cd ~/.viral-radar && /absolute/path/to/python3 ~/.viral-radar/viral-timeline.py \
  --config ~/.viral-radar/config.env \
  --state ~/.viral-radar/state.sqlite3 \
  --output-dir ~/.viral-radar/output
```

## Cron never runs (no new evidence files, empty logs)

- Confirm the managed block exists in `crontab -l`. If missing, reinstall it
  (Install step 7 in SKILL.md).
- Cron only fires while the machine is awake. Laptops that sleep between
  scheduled times simply skip those runs — this is the most common cause of
  "gaps." Confirm it with `pmset -g log | grep -E "	(Sleep|Wake|DarkWake) "`
  around the missed time: macOS drops into Deep Idle and only surfaces for
  brief (5-40s) `DarkWake` maintenance bursts, which are usually too short for
  the full webcmd → browser → X scrape → webhook round trip, so a run can be
  skipped even if a `DarkWake` happens to land near the scheduled minute. The
  actionable fix is `sudo pmset repeat wake MTWRFSU <HH:MM:SS>` (run once per
  desired wake time, local time, up to a few times if the schedule needs
  several) to force a real wake shortly before each scheduled run — check
  existing repeating wakes first with `pmset -g sched` so you don't clobber
  ones the user already set. Otherwise this needs no fix beyond
  expectation-setting (or a shorter scan interval).
- Cron runs with a minimal environment. If the log shows `command not found`
  or `webcmd was not found`, the cron line or `VIRAL_RADAR_WEBCMD` is using a
  relative name — replace with absolute paths from `which python3` /
  `which webcmd`.
- If the log shows `env: node: No such file or directory`, an absolute
  `VIRAL_RADAR_WEBCMD` path was not enough — Webcmd's binary starts with
  `#!/usr/bin/env node`, so `node` itself must still resolve on cron's `PATH`.
  Add a `PATH=<dir from which node>:/usr/bin:/bin:/usr/sbin:/sbin` line to the
  managed crontab block (see Install step 7 in SKILL.md), then rerun the
  `--test-alert` check wrapped in `env -i PATH=<same value> HOME="$HOME" ...`
  to confirm before waiting for the next real cron fire.
- On macOS, if the error log shows `Operation not permitted`, cron lacks Full
  Disk Access: System Settings → Privacy & Security → Full Disk Access → add
  `/usr/sbin/cron`.

## Webcmd failures

- **`webcmd failed: ... browser has been closed`** — the script already
  restarts the Webcmd daemon and retries once per run. If it persists across
  runs, run `webcmd daemon restart` manually, then
  `webcmd twitter whoami --window background -f json`.
- **`whoami` fails / auth errors** — the X session expired. Have the user run
  `webcmd twitter login` in a visible browser window, then confirm
  `whoami --window background` works again.
- **`webcmd timed out`** — usually a cold browser start or slow network; check
  whether subsequent runs succeed before changing anything.
- **`returned invalid JSON` / `non-list JSON`** — often a Webcmd version
  mismatch with X's page structure. Update Webcmd
  (`npm install -g @agentrhq/webcmd`) and retest.

## Webhook failures

Failure lines look like `<tweet_id>: webhook returned HTTP <code>`.

- **401/403/404** — the webhook was deleted or rotated. Have the user create a
  new one (see `notifications.md`), update `config.env`, and rerun the
  `--test-alert` check. Never print the URL while doing so.
- **429 / 5xx** — the script already retries once, honoring `Retry-After`.
  Repeated 429s across runs mean too many alerts; raise
  `VIRAL_RADAR_THRESHOLD`.
- A failed delivery is **not** marked in `state.sqlite3`, so the post is
  retried on the next run — no action needed for one-off failures.

## No alerts arriving (but runs succeed)

Check recent evidence files:

- `selected` is empty → nothing crossed the threshold. That may be correct.
  If the user wants more alerts, lower `VIRAL_RADAR_THRESHOLD` (e.g. 100 → 60)
  or raise `VIRAL_RADAR_CUTOFF_HOURS`. Explain the scoring model from
  SKILL.md's "How detection works" so the change is informed.
- `selected` has posts but `delivered` is 0 with no failures → they were
  already delivered in a previous run (dedup by tweet ID) — working as
  designed.
- Posts flagged `"boosted": true` are intentionally suppressed by the
  astroturf filter.

## Too many or duplicate alerts

- Too many: raise `VIRAL_RADAR_THRESHOLD`, or lower `VIRAL_RADAR_FRESH_HOURS`
  so fewer posts compete per run.
- Genuine duplicates of the *same* tweet should be impossible while
  `state.sqlite3` is intact — duplicates usually mean the state file was
  deleted or the cron line points at a different `--state` path than before.
  Check the managed cron block against the file that actually exists.
- Deleting `state.sqlite3` resets dedup history: every qualifying post within
  the cutoff window re-alerts on the next run. Warn the user before doing it.
