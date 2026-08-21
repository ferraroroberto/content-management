"""Hand-off (issue #212): which URLs to open, idempotence against open tabs, the watermark line, the
Notion comment write (client mocked) and the local watermark advance. No network, no Chrome."""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import db  # noqa: E402
from newsletter.triage import handoff as ho  # noqa: E402
from tests.test_triage_db import FakeSupabase  # noqa: E402


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeSupabase()
        db.set_client(self.fake)
        self.run_id = db.register_run("2026-08-14", "2026-08-22", source="cache", model="m", criteria_version="1")
        cands = [{"cid": "m1:0", "message_id": "m1", "url": "https://a.com/x?utm_source=nl", "canonical": "https://a.com/x",
                  "topic": "innovation", "suggested": "pick", "suggested_rank": 1},
                 {"cid": "m2:0", "message_id": "m2", "url": "https://b.com/y", "canonical": "https://b.com/y",
                  "topic": "innovation", "suggested": "runner", "suggested_rank": 1}]
        emails = [{"message_id": "m1", "timestamp": "2026-08-20T09:05:00+00:00"},
                  {"message_id": "m2", "timestamp": "2026-08-21T13:17:00+00:00"}]
        db.store_run_results(self.run_id, emails, cands)

    def tearDown(self) -> None:
        db.set_client(None)

    def test_urls_from_suggestions_then_decisions(self) -> None:
        urls, source = ho.ticked_urls(self.run_id)
        self.assertEqual((urls, source), (["https://a.com/x"], "suggestions"))      # canonical, not the tracking href
        db.save_decisions("2026-08-14", "2026-08-22", [
            {"canonical": "https://a.com/x", "url": "https://a.com/x?utm_source=nl", "pick": False},
            {"canonical": "https://b.com/y", "url": "https://b.com/y", "pick": True},
            {"canonical": "https://b.com/y", "url": "https://b.com/y", "pick": True},     # upsert → one row
        ])
        urls, source = ho.ticked_urls(self.run_id)
        self.assertEqual((urls, source), (["https://b.com/y"], "decisions"))

    def test_open_url_keeps_host_strips_tracking_uses_post_url_for_app_links(self) -> None:
        self.assertEqual(ho.open_url("https://www.Site.com/A/b?utm_source=x&id=7&deliveryName=NL", "https://site.com/A/b?id=7"),
                         "https://www.Site.com/A/b?id=7")
        self.assertEqual(ho.open_url("https://open.substack.com/pub/x/p/slug?utm_source=a&r=1", "https://x.substack.com/p/slug"),
                         "https://x.substack.com/p/slug")
        self.assertEqual(ho.open_url(None, "https://c.com/z"), "https://c.com/z")

    def test_plan_open_is_idempotent_on_canonical(self) -> None:
        to_open, skipped = ho.plan_open(["https://a.com/x?utm_source=nl", "https://b.com/y"],
                                        ["https://a.com/x", "chrome://newtab/"])
        self.assertEqual(to_open, ["https://b.com/y"])
        self.assertEqual(skipped, ["https://a.com/x?utm_source=nl"])

    def test_watermark_line(self) -> None:
        until = ho.window_until(self.run_id)
        self.assertEqual(until, datetime(2026, 8, 21, 13, 17, tzinfo=timezone.utc))
        line = ho.watermark_line(until, window=("2026-08-14", "2026-08-22"))
        self.assertTrue(line.startswith("until "))
        self.assertIn(" > included (triage 2026-08-14 → 2026-08-22)", line)
        local = until.astimezone()
        self.assertIn(local.strftime("%d/%m"), line)
        self.assertIn(local.strftime("%p"), line)
        self.assertNotIn(" 0", line.split(local.strftime("%d/%m"))[1][:3])      # no zero-padded hour

    def test_mark_reviewed_writes_one_comment_and_advances_state(self) -> None:
        client = unittest.mock.MagicMock()
        client.comments.create.return_value = {"id": "c-1"}
        with unittest.mock.patch.object(ho, "load_state", return_value={"reviewed_until": "2026-08-14"}), \
                unittest.mock.patch.object(ho, "save_state") as saved:
            res = ho.mark_reviewed(self.run_id, line="until Fri 21/08 3:17 PM > included", page_id="p1", client=client)
        self.assertEqual(client.comments.create.call_count, 1)
        kwargs = client.comments.create.call_args.kwargs
        self.assertEqual(kwargs["parent"], {"page_id": "p1"})
        self.assertEqual(kwargs["rich_text"][0]["text"]["content"], "until Fri 21/08 3:17 PM > included")
        self.assertEqual(res["comment_id"], "c-1")
        state = saved.call_args[0][0]
        self.assertEqual(state["reviewed_until"], "2026-08-22")
        self.assertEqual(state["watermarks"][0]["comment_id"], "c-1")

    def test_mark_reviewed_needs_page_id(self) -> None:
        with unittest.mock.patch.object(ho, "load_block", return_value={}):
            with self.assertRaises(RuntimeError):
                ho.mark_reviewed(self.run_id, line="x", client=unittest.mock.MagicMock())


if __name__ == "__main__":
    unittest.main()
