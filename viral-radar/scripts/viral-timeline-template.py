#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_HOME = Path.home() / ".viral-radar"
DEFAULT_LIMIT = 80
DEFAULT_CUTOFF_HOURS = 6.0
DEFAULT_FRESH_HOURS = 2.0
DEFAULT_THRESHOLD = 100.0
DEFAULT_EXPONENT = 0.25
DEFAULT_WEBCMD_WINDOW = "background"
RETENTION_DAYS = 30
BEGIN_MARKER = "# viral-radar begin"
END_MARKER = "# viral-radar end"


def parse_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def merged_config(path: Path) -> dict[str, str]:
    values = {key: value for key, value in os.environ.items() if key.startswith("VIRAL_RADAR_")}
    values.update(load_config(path))
    return values


def screen_name_from_author(author: object) -> str:
    if isinstance(author, str):
        return author.strip().lstrip("@")
    if isinstance(author, dict):
        return str(author.get("screenName") or author.get("username") or "").strip().lstrip("@")
    return ""


def parse_ts(row: dict[str, object]) -> datetime | None:
    value = str(row.get("created_at") or "")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def metrics_from_row(row: dict[str, object]) -> dict[str, int]:
    return {
        "likes": parse_int(row.get("likes")),
        "retweets": parse_int(row.get("retweets")),
        "replies": parse_int(row.get("replies")),
        "quotes": parse_int(row.get("quotes")),
        "bookmarks": parse_int(row.get("bookmarks")),
        "views": parse_int(row.get("views")),
    }


def run_webcmd_json(
    webcmd: str,
    args: list[str],
    timeout: int = 180,
    window: str = DEFAULT_WEBCMD_WINDOW,
) -> object:
    command = shlex.split(webcmd) + args
    if window:
        command += ["--window", window]
    command += ["-f", "json"]
    result = run_webcmd_command(command, timeout)
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("webcmd returned invalid JSON") from exc


def run_webcmd_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("webcmd was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"webcmd timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        if "Target page, context or browser has been closed" in detail:
            subprocess.run(
                [command[0], "daemon", "restart"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                return result
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise RuntimeError(f"webcmd failed: {detail[:500]}")
    return result


def fetch_tweets(webcmd: str, limit: int, window: str = DEFAULT_WEBCMD_WINDOW) -> list[dict[str, object]]:
    payload = run_webcmd_json(
        webcmd,
        ["twitter", "timeline", "--type", "for-you", "--limit", str(limit)],
        window=window,
    )
    if isinstance(payload, dict) and "data" in payload:
        payload = payload.get("data") or []
    if not isinstance(payload, list):
        raise RuntimeError("webcmd twitter timeline returned non-list JSON")
    return [row for row in payload if isinstance(row, dict)]


def fetch_followers(webcmd: str, screen_name: str, window: str = DEFAULT_WEBCMD_WINDOW) -> int:
    if not screen_name:
        return 0
    try:
        payload = run_webcmd_json(
            webcmd, ["twitter", "profile", screen_name], timeout=120, window=window
        )
    except RuntimeError:
        return 0
    if isinstance(payload, list):
        profile = payload[0] if payload else {}
    elif isinstance(payload, dict):
        profile = payload.get("data") or payload
        if isinstance(profile, list):
            profile = profile[0] if profile else {}
    else:
        profile = {}
    return parse_int(profile.get("followers") if isinstance(profile, dict) else 0)


def is_artificially_boosted(
    row: dict[str, object], follower_fetcher: Callable[[str], int]
) -> tuple[bool, str]:
    metrics = metrics_from_row(row)
    retweets = metrics["retweets"]
    quotes = metrics["quotes"]
    replies = metrics["replies"]
    amp_reply = (retweets + quotes) / replies if replies > 0 else float("inf")
    qt_rt = quotes / retweets if retweets > 0 else float("inf")
    if not (amp_reply > 2.0 or qt_rt > 1.0):
        return False, ""
    followers = follower_fetcher(screen_name_from_author(row.get("author")))
    if followers >= 20_000:
        return False, ""
    return True, f"followers={followers}"


def build_result(
    row: dict[str, object],
    now: datetime,
    follower_fetcher: Callable[[str], int],
    exponent: float = DEFAULT_EXPONENT,
) -> dict[str, object] | None:
    tweet_id = str(row.get("id") or "")
    timestamp = parse_ts(row)
    if not tweet_id or not timestamp:
        return None
    screen_name = screen_name_from_author(row.get("author"))
    metrics = metrics_from_row(row)
    engagement = (
        metrics["likes"]
        + metrics["retweets"] * 3
        + metrics["replies"] * 9
        + metrics["quotes"] * 7
        + metrics["bookmarks"] * 5
    )
    score = engagement / (metrics["views"] ** exponent) if metrics["views"] > 0 else None
    boosted, boost_reason = is_artificially_boosted(row, follower_fetcher)
    return {
        "platform": "x",
        "id": tweet_id,
        "author": f"@{screen_name}" if screen_name else "",
        "url": row.get("url")
        or (
            f"https://x.com/{screen_name}/status/{tweet_id}"
            if screen_name
            else f"https://x.com/i/web/status/{tweet_id}"
        ),
        "created_at": timestamp.isoformat(),
        "age_min": int((now - timestamp).total_seconds() / 60),
        "text": str(row.get("text") or ""),
        "metrics": metrics,
        "views": metrics["views"],
        "engagement_score": engagement,
        "normalized_score": round(score, 4) if score is not None else None,
        "boosted": boosted,
        "boost_reason": boost_reason,
    }


def select_results(
    results: list[dict[str, object]], threshold: float, fresh_hours: float
) -> list[dict[str, object]]:
    above_threshold = [
        item
        for item in results
        if item.get("normalized_score") is not None
        and float(item["normalized_score"]) > threshold
        and not item.get("boosted")
    ]
    fresh_minutes = fresh_hours * 60
    fresh = [item for item in above_threshold if int(item["age_min"]) < fresh_minutes]
    if fresh:
        fresh.sort(key=lambda item: -float(item["normalized_score"]))
        return fresh
    above_threshold.sort(key=lambda item: int(item["age_min"]))
    return above_threshold[:1]


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            tweet_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            first_detected_at TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            destination TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            last_observed_score REAL
        );
        """
    )
    conn.commit()
    return conn


def already_delivered(conn: sqlite3.Connection, tweet_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM deliveries WHERE tweet_id = ?", (tweet_id,)).fetchone()
    return row is not None


def mark_delivered(
    conn: sqlite3.Connection,
    result: dict[str, object],
    destination: str,
    now: datetime,
    attempts: int,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO deliveries(
                tweet_id, url, first_detected_at, delivered_at, destination,
                attempt_count, last_observed_score
            ) VALUES (?, ?, COALESCE(
                (SELECT first_detected_at FROM deliveries WHERE tweet_id = ?), ?
            ), ?, ?, ?, ?)
            """,
            (
                str(result["id"]),
                str(result.get("url") or ""),
                str(result["id"]),
                now.isoformat(),
                now.isoformat(),
                destination,
                attempts,
                result.get("normalized_score"),
            ),
        )


def _fmt_int(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _short_text(value: object, limit: int = 3000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "(no text)"
    return text[: limit - 3].rstrip() + "..."


def build_webhook_payload(destination: str, result: dict[str, object]) -> dict[str, object]:
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    title = f"{result.get('author') or 'Unknown'} - score {result.get('normalized_score')}"
    url = str(result.get("url") or "")
    text = _short_text(result.get("text"))
    if destination == "discord":
        return {
            "content": "Viral Radar alert",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": title,
                    "url": url,
                    "description": text,
                    "fields": [
                        {"name": "Age", "value": f"{result.get('age_min', '?')} min", "inline": True},
                        {"name": "Views", "value": _fmt_int(result.get("views")), "inline": True},
                        {
                            "name": "Engagement",
                            "value": _fmt_int(result.get("engagement_score")),
                            "inline": True,
                        },
                    ],
                }
            ],
        }
    if destination == "slack":
        return {
            "text": f"Viral Radar alert: {url}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{title}*\n<{url}|Open post>\n{text}"},
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"Age {result.get('age_min', '?')} min | "
                                f"Views {_fmt_int(result.get('views'))} | "
                                f"Engagement {_fmt_int(result.get('engagement_score'))}"
                            ),
                        }
                    ],
                },
            ],
        }
    return {"text": text, "url": url, "result": result}


def _webhook_url(destination: str, config: dict[str, str]) -> str:
    key = {
        "discord": "VIRAL_RADAR_DISCORD_WEBHOOK",
        "slack": "VIRAL_RADAR_SLACK_WEBHOOK",
        "generic": "VIRAL_RADAR_GENERIC_WEBHOOK",
    }.get(destination)
    if not key:
        raise RuntimeError("VIRAL_RADAR_DESTINATION must be discord, slack, or generic")
    value = config.get(key, "").strip()
    if not value:
        raise RuntimeError(f"{key} is required")
    return value


def _discord_wait_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.lower() != "wait"]
    query.append(("wait", "true"))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def send_webhook(
    destination: str,
    url: str,
    result: dict[str, object],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    target = _discord_wait_url(url) if destination == "discord" else url
    body = json.dumps(build_webhook_payload(destination, result), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        target,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "ViralRadar/1.0"},
        method="POST",
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if 200 <= response.status < 300:
                    return attempt
                raise RuntimeError(f"webhook returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if attempt == 1 and (exc.code == 429 or 500 <= exc.code < 600):
                retry_after = exc.headers.get("Retry-After", "1")
                try:
                    delay = max(0.0, float(retry_after))
                except ValueError:
                    delay = 1.0
                exc.close()
                sleep(delay)
                continue
            code = exc.code
            exc.close()
            raise RuntimeError(f"webhook returned HTTP {code}") from exc
        except urllib.error.URLError as exc:
            if attempt == 1:
                sleep(1)
                continue
            raise RuntimeError(f"webhook request failed: {exc.reason}") from exc
    raise RuntimeError("webhook retry failed")


def evidence_result(result: dict[str, object]) -> dict[str, object]:
    return {
        key: result.get(key)
        for key in (
            "platform",
            "id",
            "author",
            "url",
            "created_at",
            "age_min",
            "views",
            "engagement_score",
            "normalized_score",
            "text",
            "metrics",
        )
    }


def write_evidence(
    output_dir: Path,
    now: datetime,
    selected: list[dict[str, object]],
    delivered: list[str],
    failures: list[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{stamp}.json"
    counter = 1
    while path.exists():
        counter += 1
        path = output_dir / f"{stamp}-{counter}.json"
    payload = {
        "observed_at": now.isoformat(),
        "selected": [evidence_result(item) for item in selected],
        "delivered": len(delivered),
        "delivered_ids": delivered,
        "failures": failures,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def prune(conn: sqlite3.Connection, output_dir: Path, now: datetime, retention_days: int) -> None:
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    with conn:
        conn.execute("DELETE FROM deliveries WHERE delivered_at < ?", (cutoff,))
    if not output_dir.exists():
        return
    cutoff_ts = (now - timedelta(days=retention_days)).timestamp()
    for path in output_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff_ts:
                path.unlink()
        except OSError:
            pass


def replace_managed_cron(existing: str, command: str, schedule: str) -> str:
    lines = existing.splitlines()
    kept: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            inside = True
            continue
        if line.strip() == END_MARKER:
            inside = False
            continue
        if not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [BEGIN_MARKER, f"{schedule} {command}", END_MARKER]
    return "\n".join(kept + ([""] if kept else []) + block) + "\n"


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def run_once(args: argparse.Namespace) -> int:
    config = merged_config(args.config)
    now = parse_now(args.now)
    destination = config.get("VIRAL_RADAR_DESTINATION", "").strip().lower()
    webhook_url = _webhook_url(destination, config)
    if args.test_alert:
        result = {
            "platform": "x",
            "id": "__viral_radar_test__",
            "author": "@viral-radar",
            "url": "https://x.com/",
            "created_at": now.isoformat(),
            "age_min": 0,
            "text": "Viral Radar test alert",
            "metrics": {},
            "views": 0,
            "engagement_score": 0,
            "normalized_score": 0,
            "boosted": False,
            "boost_reason": "",
        }
        conn = init_db(args.state)
        try:
            attempts = send_webhook(destination, webhook_url, result)
            mark_delivered(conn, result, destination, now, attempts)
            write_evidence(args.output_dir, now, [result], [str(result["id"])], [])
        finally:
            conn.close()
        return 0

    limit = int(parse_float(config.get("VIRAL_RADAR_LIMIT"), DEFAULT_LIMIT))
    cutoff_hours = parse_float(config.get("VIRAL_RADAR_CUTOFF_HOURS"), DEFAULT_CUTOFF_HOURS)
    fresh_hours = parse_float(config.get("VIRAL_RADAR_FRESH_HOURS"), DEFAULT_FRESH_HOURS)
    threshold = parse_float(config.get("VIRAL_RADAR_THRESHOLD"), DEFAULT_THRESHOLD)
    exponent = parse_float(config.get("VIRAL_RADAR_VIEW_EXPONENT"), DEFAULT_EXPONENT)
    retention_days = int(parse_float(config.get("VIRAL_RADAR_RETENTION_DAYS"), RETENTION_DAYS))
    webcmd = args.webcmd or config.get("VIRAL_RADAR_WEBCMD", "webcmd")
    window = config.get("VIRAL_RADAR_WEBCMD_WINDOW", DEFAULT_WEBCMD_WINDOW).strip().lower()
    if window not in ("foreground", "background"):
        raise RuntimeError("VIRAL_RADAR_WEBCMD_WINDOW must be foreground or background")

    cutoff = now - timedelta(hours=cutoff_hours)
    rows = fetch_tweets(webcmd, limit, window)
    seen: set[str] = set()
    results: list[dict[str, object]] = []
    for row in rows:
        tweet_id = str(row.get("id") or "")
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)
        timestamp = parse_ts(row)
        if not timestamp or timestamp < cutoff:
            continue
        result = build_result(row, now, lambda name: fetch_followers(webcmd, name, window), exponent)
        if result:
            results.append(result)

    selected = select_results(results, threshold, fresh_hours)
    conn = init_db(args.state)
    delivered: list[str] = []
    failures: list[str] = []
    try:
        prune(conn, args.output_dir, now, retention_days)
        pending = [item for item in selected if not already_delivered(conn, str(item["id"]))]
        for item in pending:
            try:
                attempts = send_webhook(destination, webhook_url, item)
                mark_delivered(conn, item, destination, now, attempts)
                delivered.append(str(item["id"]))
            except RuntimeError as exc:
                failures.append(f"{item['id']}: {exc}")
        write_evidence(args.output_dir, now, selected, delivered, failures)
    finally:
        conn.close()
    if failures:
        print(f"viral-radar: {len(failures)} delivery failure(s): {'; '.join(failures)}", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Viral Radar X timeline scan.")
    parser.add_argument("--config", type=Path, default=DEFAULT_HOME / "config.env")
    parser.add_argument("--state", type=Path, default=DEFAULT_HOME / "state.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_HOME / "output")
    parser.add_argument("--webcmd", default="")
    parser.add_argument("--now", default="")
    parser.add_argument("--test-alert", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run_once(parse_args(argv))
    except (RuntimeError, sqlite3.Error, OSError, ValueError) as exc:
        print(f"viral-radar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
