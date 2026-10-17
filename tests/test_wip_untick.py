"""Characterization tests for the WIP-checkbox untick in every planning scheduler (issue #406).

Instagram, LinkedIn, the shared single-post loop (Twitter/Threads) and Videos
each untick the editorial row's WIP checkbox after a successful LIVE schedule,
and a failed Notion write only logs a warning — it never changes the row's
reported status or the exit code. Each scheduler keeps its own gate (IG: both
legs LIVE; LI: ``"LIVE scheduled"`` in the status; single-post: ``"LIVE"``;
Videos: every platform OK and link SB populated); these pin gate + write +
failure handling for all four.

No browser, no Notion: sessions, payloads and the Notion write are stubs.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import argparse
import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from planning import _scheduler_main as sm
from planning._wip_rows import ScheduleRow
from planning.instagram import schedule_instagram_posts as ig
from planning.linkedin import schedule_linkedin_posts as li
from planning.videos import schedule_videos_posts as svp
from planning.videos.videos_ledger import TagAlongLedger

LOG = logging.getLogger("test_wip_untick")

ED_COLS = {"wip_checkbox": "wip", "title_day": "day", "post_url": "link"}
CFG = {"editorial_db_id": "0" * 32, "editorial_columns": ED_COLS,
       "feed_url": "https://feed", "dry_run_default": True}


def _args(**over) -> argparse.Namespace:
    base = dict(week_start=None, date=None, all_wip=True, dry_run=False, live=True,
                force=False, debug=False)
    base.update(over)
    return argparse.Namespace(**base)


def _patch_set_field(test: unittest.TestCase, module) -> mock.Mock:
    """One mock for the Notion write wherever the untick looks it up.

    The write may live in the scheduler module or in ``_scheduler_main``;
    patching both with the same mock pins the behaviour, not the location.
    """
    spy = mock.Mock()
    for target in {module, sm}:
        if hasattr(target, "set_field"):
            patcher = mock.patch.object(target, "set_field", spy)
            patcher.start()
            test.addCleanup(patcher.stop)
    return spy


class FakeSession:
    def __init__(self, cfg):
        self.page = mock.Mock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def goto_with_login_check(self, url):
        return None

    def screenshot_failure(self, name):
        return f"{name}.png"


def _schedule_row(page_id: str = "p1") -> ScheduleRow:
    return ScheduleRow(page_id, date(2026, 1, 5), ["i"], "cap", None)


class InstagramUntickTests(unittest.TestCase):
    def setUp(self):
        row = _schedule_row()
        row.thread_ig = None
        payload = SimpleNamespace(image_paths=[Path("a.png")], caption="cap")
        for name, value in {
            "parse_args": lambda: _args(),
            "configure_logger": lambda *a, **k: None,
            "load_instagram_config": lambda: CFG,
            "load_notion_token": lambda: "tok",
            "notion_or_none": lambda *a, **k: object(),
            "fetch_wip_ig_rows": lambda *a: [row],
            "resolve_story_payload": lambda *a: payload,
            "resolve_post_payload": lambda *a: payload,
            "InstagramSession": FakeSession,
            "dismiss_meta_verified_modal": lambda page: None,
            "return_to_planner": lambda *a: None,
            "cancel_composer": lambda page: None,
        }.items():
            patcher = mock.patch.object(ig, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.story = mock.patch.object(ig, "schedule_story", return_value="story:LIVE").start()
        self.post = mock.patch.object(ig, "schedule_post", return_value="post:LIVE").start()
        self.addCleanup(mock.patch.stopall)
        self.set_field = _patch_set_field(self, ig)

    def test_fully_live_day_unticks_wip(self):
        code, results = ig.main()
        self.assertEqual((code, results[0]["status"]), (0, "LIVE"))
        self.set_field.assert_called_once()
        self.assertEqual(self.set_field.call_args.args[1:], ("p1", "wip_checkbox", False, ED_COLS, "checkbox"))

    def test_a_failed_leg_keeps_wip_checked(self):
        self.story.side_effect = RuntimeError("story composer stuck")
        code, results = ig.main()
        self.assertEqual((code, results[0]["status"]), (11, "FAIL"))
        self.set_field.assert_not_called()

    def test_dry_run_never_unticks(self):
        with mock.patch.object(ig, "parse_args", lambda: _args(live=False, dry_run=True)):
            code, results = ig.main()
        self.assertEqual((code, results[0]["status"]), (0, "DRY"))
        self.set_field.assert_not_called()

    def test_failed_untick_only_warns(self):
        self.set_field.side_effect = RuntimeError("notion 502")
        with self.assertLogs("instagram_schedule", level="WARNING") as logs:
            code, results = ig.main()
        self.assertEqual((code, results[0]["status"]), (0, "LIVE"))
        self.assertTrue(any("failed to untick WIP-IG" in line for line in logs.output), logs.output)


class LinkedInUntickTests(unittest.TestCase):
    def setUp(self):
        row = _schedule_row()
        row.route = "illustration"
        for name, value in {
            "parse_args": lambda: _args(),
            "configure_logger": lambda *a, **k: None,
            "load_linkedin_config": lambda: CFG,
            "load_notion_token": lambda: "tok",
            "notion_or_none": lambda *a, **k: object(),
            "fetch_wip_li_rows": lambda *a: [row],
            "LinkedInSession": FakeSession,
        }.items():
            patcher = mock.patch.object(li, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.schedule = mock.patch.object(
            li, "schedule_one_row", return_value="20260105: LIVE scheduled").start()
        self.addCleanup(mock.patch.stopall)
        self.set_field = _patch_set_field(self, li)

    def test_live_scheduled_row_unticks_wip(self):
        code, results = li.main()
        self.assertEqual((code, results[0]["status"]), (0, "LIVE"))
        self.assertEqual(self.set_field.call_args.args[1:], ("p1", "wip_checkbox", False, ED_COLS, "checkbox"))

    def test_status_without_live_scheduled_keeps_wip_checked(self):
        self.schedule.return_value = "20260105: skipped (already queued)"
        li.main()
        self.set_field.assert_not_called()

    def test_failed_untick_only_warns(self):
        self.set_field.side_effect = RuntimeError("notion 502")
        with self.assertLogs("linkedin_schedule", level="WARNING") as logs:
            code, results = li.main()
        self.assertEqual((code, results[0]["status"]), (0, "LIVE"))
        self.assertTrue(any("failed to untick WIP-LI" in line for line in logs.output), logs.output)


class SinglePostUntickTests(unittest.TestCase):
    def test_failed_untick_only_warns(self):
        set_field = _patch_set_field(self, sm)
        set_field.side_effect = RuntimeError("notion 502")
        payload = SimpleNamespace(image_path=Path("img.png"), caption="cap")
        with mock.patch.object(sm, "notion_or_none", return_value=object()), \
                self.assertLogs(LOG, level="WARNING") as logs:
            code, results = sm.run_single_post_scheduler(
                _args(), platform="TW", log=LOG, cfg=CFG, load_token=lambda: "tok",
                session_cls=FakeSession, fetch_rows=lambda *a: [_schedule_row()],
                resolve_payload=lambda *a: payload,
                schedule_post=lambda *a, **k: "post:LIVE",
                cancel_composer=lambda page: None, return_home=lambda *a: None,
                login_settle_ms=0)
        self.assertEqual((code, results[0]["status"]), (0, "LIVE"))
        self.assertTrue(any("failed to untick WIP-TW" in line for line in logs.output), logs.output)


class VideosUntickTests(unittest.TestCase):
    def _completed_row(self) -> svp._RowState:
        state = svp._RowState(
            page_id="page-1", day=date(2026, 8, 25), payload=None,
            link_status={"li": None, "ig": None, "tw": None, "th": None, "sb": "https://sb.example/x"},
            in_scope={"li": True, "ig": True, "tw": True, "th": True},
        )
        for p in ("li", "ig", "tw", "th"):
            state.driver_status[p] = "LIVE"
        return state

    def _decide(self, state, *, dry_run: bool = False):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TagAlongLedger(Path(tmp) / "ledger.json")
            return svp._maybe_untick_wip(mock.MagicMock(), {"wip_checkbox": "WIP Vd"},
                                         state, dry_run, ledger)

    def test_all_ok_unticks(self):
        set_field = _patch_set_field(self, svp)
        self.assertEqual(self._decide(self._completed_row()),
                         ("unticked", "all platforms OK and link SB(v) populated"))
        self.assertEqual(set_field.call_args.args[1:],
                         ("page-1", "wip_checkbox", False, {"wip_checkbox": "WIP Vd"}, "checkbox"))

    def test_failed_write_reports_untick_failed(self):
        set_field = _patch_set_field(self, svp)
        set_field.side_effect = RuntimeError("notion 502")
        with self.assertLogs("videos_schedule", level="WARNING") as logs:
            action, reason = self._decide(self._completed_row())
        self.assertEqual((action, reason), ("untick-failed", "Notion write raised: notion 502"))
        self.assertTrue(any("failed to untick WIP-Video" in line for line in logs.output), logs.output)

    def test_dry_run_never_writes(self):
        set_field = _patch_set_field(self, svp)
        self.assertEqual(self._decide(self._completed_row(), dry_run=True), ("dry-run", "dry-run mode"))
        set_field.assert_not_called()


if __name__ == "__main__":
    unittest.main()
