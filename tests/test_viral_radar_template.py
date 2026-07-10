from __future__ import annotations

import json
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from threading import Thread


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "viral-radar" / "scripts" / "viral-timeline-template.py"


def load_template() -> dict[str, object]:
    return runpy.run_path(str(TEMPLATE))


def tweet(now: datetime) -> dict[str, object]:
    return {
        "id": "2001",
        "author": {"screenName": "radar"},
        "created_at": now.isoformat(),
        "text": "Fast-moving post worth seeing",
        "likes": 900,
        "retweets": 20,
        "replies": 60,
        "quotes": 10,
        "bookmarks": 30,
        "views": 10_000,
        "url": "https://x.com/radar/status/2001",
    }


class FakeWebhook:
    def __init__(self, statuses: list[int] | None = None):
        self.statuses = statuses or [200]
        self.requests: list[dict[str, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append(
                    {
                        "path": self.path,
                        "json": json.loads(body.decode("utf-8")),
                    }
                )
                status = outer.statuses.pop(0) if outer.statuses else 200
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeWebhook":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/hook"


class ViralRadarTemplateTests(unittest.TestCase):
    def test_selection_keeps_fresh_high_scoring_posts(self) -> None:
        module = load_template()
        select_results = module["select_results"]
        selected = select_results(
            [
                {"id": "old", "age_min": 240, "normalized_score": 300, "boosted": False},
                {"id": "fresh", "age_min": 30, "normalized_score": 250, "boosted": False},
                {"id": "boosted", "age_min": 10, "normalized_score": 500, "boosted": True},
            ],
            100,
            2,
        )
        self.assertEqual([item["id"] for item in selected], ["fresh"])

    def test_payloads_support_discord_and_slack_without_mentions(self) -> None:
        module = load_template()
        build_payload = module["build_webhook_payload"]
        result = {
            "author": "@radar",
            "id": "2001",
            "url": "https://x.com/radar/status/2001",
            "text": "hello <!channel>",
            "age_min": 12,
            "views": 10000,
            "engagement_score": 900,
            "normalized_score": 180,
            "metrics": {"likes": 1, "replies": 2, "retweets": 3},
        }

        discord = build_payload("discord", result)
        slack = build_payload("slack", result)

        self.assertEqual(discord["allowed_mentions"], {"parse": []})
        self.assertIn("embeds", discord)
        self.assertIn("blocks", slack)
        self.assertIn("https://x.com/radar/status/2001", json.dumps(slack))

    def test_cron_block_replacement_preserves_unrelated_entries(self) -> None:
        module = load_template()
        replace_managed_cron = module["replace_managed_cron"]
        existing = "5 * * * * echo keep\n# viral-radar begin\nold\n# viral-radar end\n"

        updated = replace_managed_cron(existing, "/usr/bin/python3 /tmp/viral.py", "*/15 * * * *")

        self.assertIn("5 * * * * echo keep", updated)
        self.assertIn("*/15 * * * * /usr/bin/python3 /tmp/viral.py", updated)
        self.assertNotIn("\nold\n", updated)

    def test_main_delivers_once_and_writes_secret_free_evidence(self) -> None:
        module = load_template()
        main = module["main"]
        now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            timeline = root / "timeline.json"
            timeline.write_text(json.dumps([tweet(now)]), encoding="utf-8")
            webcmd = root / "webcmd"
            webcmd.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())\n",
                encoding="utf-8",
            )
            webcmd.chmod(0o700)
            output = root / "output"
            state = root / "state.sqlite3"

            with FakeWebhook() as webhook:
                config = root / "config.env"
                config.write_text(
                    "\n".join(
                        [
                            "VIRAL_RADAR_DESTINATION=discord",
                            f"VIRAL_RADAR_DISCORD_WEBHOOK={webhook.url}",
                            "VIRAL_RADAR_LIMIT=80",
                        ]
                    ),
                    encoding="utf-8",
                )
                args = [
                    "--config",
                    str(config),
                    "--state",
                    str(state),
                    "--output-dir",
                    str(output),
                    "--webcmd",
                    f"{webcmd} {timeline}",
                    "--now",
                    now.isoformat(),
                ]

                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)

            self.assertEqual(len(webhook.requests), 1)
            evidence_files = list(output.glob("*.json"))
            self.assertEqual(len(evidence_files), 2)
            evidence = "\n".join(path.read_text(encoding="utf-8") for path in evidence_files)
            self.assertIn('"delivered": 1', evidence)
            self.assertNotIn(webhook.url, evidence)

    def test_failed_delivery_retries_next_run(self) -> None:
        module = load_template()
        main = module["main"]
        now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            timeline = root / "timeline.json"
            timeline.write_text(json.dumps([tweet(now)]), encoding="utf-8")
            webcmd = root / "webcmd"
            webcmd.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())\n",
                encoding="utf-8",
            )
            webcmd.chmod(0o700)
            output = root / "output"
            state = root / "state.sqlite3"

            with FakeWebhook([500, 500]) as failing:
                config = root / "config.env"
                config.write_text(
                    f"VIRAL_RADAR_DESTINATION=discord\nVIRAL_RADAR_DISCORD_WEBHOOK={failing.url}\n",
                    encoding="utf-8",
                )
                args = [
                    "--config",
                    str(config),
                    "--state",
                    str(state),
                    "--output-dir",
                    str(output),
                    "--webcmd",
                    f"{webcmd} {timeline}",
                    "--now",
                    now.isoformat(),
                ]
                with redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 1)

            with FakeWebhook() as succeeding:
                config.write_text(
                    f"VIRAL_RADAR_DESTINATION=discord\nVIRAL_RADAR_DISCORD_WEBHOOK={succeeding.url}\n",
                    encoding="utf-8",
                )
                self.assertEqual(main(args), 0)

            self.assertEqual(len(succeeding.requests), 1)

    def test_test_alert_posts_without_calling_webcmd_and_records_delivery(self) -> None:
        module = load_template()
        main = module["main"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state.sqlite3"
            output = root / "output"
            with FakeWebhook() as webhook:
                config = root / "config.env"
                config.write_text(
                    f"VIRAL_RADAR_DESTINATION=discord\nVIRAL_RADAR_DISCORD_WEBHOOK={webhook.url}\n",
                    encoding="utf-8",
                )
                rc = main(
                    [
                        "--config",
                        str(config),
                        "--state",
                        str(state),
                        "--output-dir",
                        str(output),
                        "--webcmd",
                        "/path/that/must/not/run",
                        "--test-alert",
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(len(webhook.requests), 1)
        payload = webhook.requests[0]["json"]
        self.assertIn("Viral Radar test alert", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
