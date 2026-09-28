"""Shared scheduler prelude + single-post loop + WIP-page iterator (issue #322).

``planning/_scheduler_main.py`` and ``planning/_wip_rows.iter_wip_pages`` replace
copies that lived in all five planning schedulers. No browser, no Notion, no
network: Notion is a fake ``databases.query`` and the Playwright session is a stub.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import argparse
import logging
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from planning import _scheduler_main as sm
from planning._session_base import LoginRequiredError
from planning._wip_rows import ScheduleRow, fetch_wip_rows, iter_wip_pages

LOG = logging.getLogger("test_scheduler_main")

ED_COLS = {
    "wip_checkbox": "wip",
    "title_day": "day",
    "illustration_rel": "illust",
    "caption_text": "text",
    "post_url": "link",
}


def _args(**over) -> argparse.Namespace:
    base = dict(week_start=None, date=None, all_wip=False, dry_run=False, live=False,
                force=False, debug=False)
    base.update(over)
    return argparse.Namespace(**base)


def _page(page_id: str, title: str, *, url: str | None = None, illust: bool = True) -> dict:
    return {
        "id": page_id,
        "properties": {
            "day": {"title": [{"plain_text": title}]},
            "illust": {"relation": [{"id": "ill-1"}] if illust else []},
            "text": {"rich_text": [{"plain_text": "caption"}]},
            "link": {"type": "url", "url": url},
        },
    }


class FakeNotion:
    """``databases.query`` returns the pages whose day title matches (or all)."""

    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.databases = SimpleNamespace(query=self._query)
        self.filters: list[dict] = []

    def _query(self, **kwargs) -> dict:
        flt = kwargs["filter"]
        self.filters.append(flt)
        if "and" in flt:
            title = flt["and"][0]["title"]["equals"]
            hits = [p for p in self.pages if p["properties"]["day"]["title"][0]["plain_text"] == title]
        else:
            hits = list(self.pages)
        return {"results": hits, "has_more": False}


class ResolveScopeTests(unittest.TestCase):
    def test_live_beats_config_default(self):
        scope = sm.resolve_scope(_args(live=True, date="20260105"), {"dry_run_default": True},
                                 wip_label="WIP-TW", log=LOG)
        self.assertFalse(scope.dry_run)
        self.assertEqual(scope.target_days, [date(2026, 1, 5)])

    def test_dry_run_flag_and_config_default(self):
        self.assertTrue(sm.resolve_scope(_args(dry_run=True), {}, wip_label="x", log=LOG).dry_run)
        self.assertTrue(sm.resolve_scope(_args(), {}, wip_label="x", log=LOG).dry_run)
        self.assertFalse(
            sm.resolve_scope(_args(), {"dry_run_default": False}, wip_label="x", log=LOG).dry_run)

    def test_all_wip_has_no_target_days(self):
        scope = sm.resolve_scope(_args(all_wip=True), {}, wip_label="WIP-TH", log=LOG)
        self.assertIsNone(scope.target_days)

    def test_all_wip_with_date_is_refused(self):
        self.assertIsNone(
            sm.resolve_scope(_args(all_wip=True, date="20260105"), {}, wip_label="x", log=LOG))

    def test_week_start_yields_seven_days(self):
        scope = sm.resolve_scope(_args(week_start="2026-01-05"), {}, wip_label="x", log=LOG)
        self.assertEqual(len(scope.target_days), 7)
        self.assertEqual(scope.target_days[0], date(2026, 1, 5))
        self.assertEqual(scope.target_days[-1], date(2026, 1, 11))


class DropAlreadyScheduledTests(unittest.TestCase):
    def setUp(self):
        self.rows = [SimpleNamespace(existing_post_url=None), SimpleNamespace(existing_post_url="u")]

    def test_drops_populated_links(self):
        kept = sm.drop_already_scheduled(self.rows, force=False, link_label="TW", log=LOG)
        self.assertEqual(kept, [self.rows[0]])

    def test_force_keeps_everything(self):
        kept = sm.drop_already_scheduled(self.rows, force=True, link_label="TW", log=LOG)
        self.assertEqual(kept, self.rows)


class IterWipPagesTests(unittest.TestCase):
    def test_per_day_query_uses_title_filter_and_default_day(self):
        notion = FakeNotion([_page("a", "20260105"), _page("b", "20260106")])
        got = list(iter_wip_pages(notion, "db", wip_col="wip", title_col="day",
                                  days=[date(2026, 1, 5)], logger=LOG))
        self.assertEqual([(p["id"], d) for p, d in got], [("a", date(2026, 1, 5))])
        self.assertEqual(notion.filters[0]["and"][0], {"property": "day", "title": {"equals": "20260105"}})

    def test_all_wip_parses_day_from_title_and_skips_bad_titles(self):
        notion = FakeNotion([_page("a", "20260105"), _page("bad", "scratch"), _page("empty", "")])
        with self.assertLogs(LOG, level="WARNING") as logs:
            got = list(iter_wip_pages(notion, "db", wip_col="wip", title_col="day",
                                      days=None, logger=LOG))
        self.assertEqual([p["id"] for p, _ in got], ["a"])
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(notion.filters, [{"property": "wip", "checkbox": {"equals": True}}])

    def test_fetch_wip_rows_builds_sorted_schedule_rows(self):
        notion = FakeNotion([_page("late", "20260107", url="https://x"), _page("early", "20260105")])
        rows = fetch_wip_rows(notion, "db", ED_COLS, None, logger=LOG)
        self.assertEqual([r.page_id for r in rows], ["early", "late"])
        self.assertIsNone(rows[0].existing_post_url)
        self.assertEqual(rows[1].existing_post_url, "https://x")
        self.assertEqual(rows[0].illustration_ids, ["ill-1"])
        self.assertEqual(rows[0].text, "caption")


class FakeSession:
    """Context-manager stand-in for the Playwright session classes."""

    login_error: Exception | None = None

    def __init__(self, cfg):
        self.page = mock.Mock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def goto_with_login_check(self, url):
        if FakeSession.login_error:
            raise FakeSession.login_error

    def screenshot_failure(self, name):
        return f"{name}.png"


class RunSinglePostSchedulerTests(unittest.TestCase):
    def setUp(self):
        FakeSession.login_error = None
        self.cfg = {"editorial_db_id": "0" * 32, "editorial_columns": ED_COLS,
                    "feed_url": "https://feed", "dry_run_default": True}
        self.notion = object()
        self.rows = [
            ScheduleRow("p1", date(2026, 1, 5), ["i"], "cap", None),
            ScheduleRow("p2", date(2026, 1, 6), ["i"], "cap", "https://already"),
        ]
        self.payload = SimpleNamespace(image_path=Path("img.png"), caption="cap")
        self.schedule_post = mock.Mock(return_value="post:LIVE")
        self.return_home = mock.Mock()
        self.cancel = mock.Mock()
        self.set_field = mock.patch.object(sm, "set_field").start()
        mock.patch.object(sm, "notion_or_none", return_value=self.notion).start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, args, *, fetch_rows=None, resolve_payload=None, **kw):
        return sm.run_single_post_scheduler(
            args, platform="TW", log=LOG, cfg=self.cfg, load_token=lambda: "tok",
            session_cls=FakeSession,
            fetch_rows=fetch_rows or (lambda *a: list(self.rows)),
            resolve_payload=resolve_payload or (lambda *a: self.payload),
            schedule_post=self.schedule_post, cancel_composer=self.cancel,
            return_home=self.return_home, **kw)

    def test_live_run_schedules_dedups_and_unticks(self):
        code, results = self._run(_args(live=True))
        self.assertEqual(code, 0)
        self.assertEqual([(r["day"], r["status"]) for r in results], [("20260105", "LIVE")])
        self.schedule_post.assert_called_once()
        self.set_field.assert_called_once()
        self.assertEqual(self.set_field.call_args.args[1:4], ("p1", "wip_checkbox", False))

    def test_dry_run_never_unticks(self):
        code, results = self._run(_args(dry_run=True))
        self.assertEqual((code, results[0]["status"]), (0, "DRY"))
        self.set_field.assert_not_called()

    def test_force_schedules_already_linked_rows(self):
        _, results = self._run(_args(live=True, force=True))
        self.assertEqual(len(results), 2)

    def test_after_login_hook_runs(self):
        hook = mock.Mock()
        self._run(_args(dry_run=True), after_login=hook)
        hook.assert_called_once()

    def test_no_rows_is_a_clean_noop(self):
        self.assertEqual(self._run(_args(), fetch_rows=lambda *a: []), (0, []))

    def test_payload_failure_for_every_row_exits_11(self):
        def boom(*a):
            raise FileNotFoundError("missing")
        code, results = self._run(_args(live=True), resolve_payload=boom)
        self.assertEqual(code, 11)
        self.assertEqual(results[0]["status"], "FAIL")

    def test_login_required_exits_4(self):
        FakeSession.login_error = LoginRequiredError("login")
        code, results = self._run(_args(live=True))
        self.assertEqual(code, 4)
        self.assertEqual(results[0]["status"], "LOGIN-REQUIRED")

    def test_row_failure_is_reported_and_exits_11(self):
        self.schedule_post.side_effect = RuntimeError("composer never opened")
        code, results = self._run(_args(live=True))
        self.assertEqual(code, 11)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(self.schedule_post.call_count, 2)  # attempt_row retried once
        self.set_field.assert_not_called()

    def test_conflicting_flags_exit_2(self):
        self.assertEqual(self._run(_args(all_wip=True, date="20260105")), (2, []))


if __name__ == "__main__":
    unittest.main()
