# Viral Radar

Viral Radar is an agent skill that installs a local cron job which scans your
authenticated X For You timeline with Webcmd and sends new viral-post alerts
to Discord, Slack, or a generic webhook.

Install the skill from this checkout:

```bash
# Codex
skill_dir="${CODEX_HOME:-$HOME/.codex}/skills"; mkdir -p "$skill_dir"; rm -rf "$skill_dir/viral-radar"; cp -R viral-radar "$skill_dir/"

# Claude Code
skill_dir="$HOME/.claude/skills"; mkdir -p "$skill_dir"; rm -rf "$skill_dir/viral-radar"; cp -R viral-radar "$skill_dir/"
```

Then start setup in your agent:

```text
Set up viral post alerts from my X timeline to Discord.
```

The scheduled job is local Python code under `~/.viral-radar/`. It does not
run an LLM or an agent on a schedule.

## Development

Run the template's unit tests:

```bash
python3 -m unittest discover -s tests
```
