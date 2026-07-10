# Viral Radar

Viral Radar installs a local cron job that scans your authenticated X For You
timeline with Webcmd and sends new viral-post alerts to Discord, Slack, or a
generic webhook.

Install the bundled Codex skill from this checkout:

```bash
skill_dir="${CODEX_HOME:-$HOME/.codex}/skills"; mkdir -p "$skill_dir"; rm -rf "$skill_dir/viral-radar"; cp -R viral-radar "$skill_dir/"
```

Then start setup in Codex:

```text
Set up viral posts from my X timeline to Discord.
```

The scheduled job is local Python code under `~/.viral-radar/`. It does not run
Codex, an LLM, or an agent on a schedule.
