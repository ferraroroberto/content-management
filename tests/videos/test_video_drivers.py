"""Characterization tests for the Threads / Twitter / Instagram video drivers' ``run()`` (issue #406).

Each driver opens one platform session, checks login once (every row
LOGIN-REQUIRED when it fails), settles, then schedules row by row: a
``RuntimeError`` / Playwright timeout records FAIL with a screenshot,
cancels the composer, returns home and moves on. These pin that loop for
all three so sharing it keeps every result row and side effect unchanged.

No browser, no Notion: the session, config and per-platform callbacks are stubs.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from playwright.sync_api import TimeoutError as PWTimeoutError

from planning._session_base import LoginRequiredError
from planning.videos import videos_instagram, videos_threads, videos_twitter

CFG = {"feed_url": "https://feed"}


class FakeSession:
    login_error: Exception | None = None
    instances: list["FakeSession"] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.page = mock.Mock(name="page")
        self.events: list = []
        self.page.wait_for_timeout.side_effect = lambda ms: self.events.append(("wait", ms))
        FakeSession.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.events.append("closed")
        return False

    def goto_with_login_check(self, url):
        self.events.append(("login", url))
        if FakeSession.login_error:
            raise FakeSession.login_error

    def screenshot_failure(self, name):
        return f"{name}.png"


def _rows(*days: str) -> list:
    return [SimpleNamespace(day_title=d, day=date(2026, 1, 5)) for d in days]


class _DriverCase:
    """Shared expectations; each subclass names its module and platform pieces."""

    module = None
    session_attr = ""
    loader_attr = ""
    home_attr = ""
    slug = ""
    label = ""
    settle_ms: tuple = ()
    modal_attr = None

    def setUp(self):
        FakeSession.login_error = None
        FakeSession.instances = []
        self.calls: list = []
        self.schedule = mock.Mock(side_effect=lambda session, cfg, vcfg, row, dry_run: f"{row.day_title}:ok")
        patches = {
            self.session_attr: FakeSession,
            self.loader_attr: mock.Mock(return_value=CFG),
            "schedule_one_video": self.schedule,
            self.home_attr: mock.Mock(side_effect=lambda page, url: self._event(("home", url))),
            "cancel_composer": mock.Mock(side_effect=lambda page: self._event("cancel")),
        }
        if self.modal_attr:
            patches[self.modal_attr] = mock.Mock(side_effect=lambda page: self._event("modal"))
        for name, value in patches.items():
            patcher = mock.patch.object(self.module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.loader = patches[self.loader_attr]

    def _event(self, ev) -> None:
        FakeSession.instances[-1].events.append(ev)

    def _run(self, rows, *, dry_run=False):
        return self.module.run(rows, {"video": 1}, dry_run=dry_run)

    def test_no_rows_opens_nothing(self):
        self.assertEqual(self._run([]), [])
        self.loader.assert_not_called()
        self.assertEqual(FakeSession.instances, [])

    def test_live_rows(self):
        out = self._run(_rows("20260105", "20260106"))
        self.assertEqual(out, [{"day": "20260105", "status": "LIVE", "detail": "20260105:ok"},
                               {"day": "20260106", "status": "LIVE", "detail": "20260106:ok"}])
        self.assertEqual(self.schedule.call_args.args[1:3], (CFG, {"video": 1}))
        session = FakeSession.instances[0]
        self.assertEqual(session.cfg, CFG)
        settle = [("wait", ms) for ms in self.settle_ms]
        modal = ["modal"] if self.modal_attr else []
        expected_prelude = [("login", "https://feed"), settle[0], *modal, *settle[1:]]
        self.assertEqual(session.events, [*expected_prelude, ("home", "https://feed"), ("home", "https://feed"), "closed"])

    def test_dry_rows(self):
        out = self._run(_rows("20260105"), dry_run=True)
        self.assertEqual(out[0]["status"], "DRY")
        self.assertTrue(self.schedule.call_args.kwargs["dry_run"])

    def test_login_required_marks_every_row(self):
        FakeSession.login_error = LoginRequiredError("please log in")
        with self.assertLogs(self.module.logger, level="ERROR") as logs:
            out = self._run(_rows("20260105", "20260106"))
        self.assertEqual(out, [{"day": d, "status": "LOGIN-REQUIRED", "detail": "please log in"}
                               for d in ("20260105", "20260106")])
        self.schedule.assert_not_called()
        self.assertTrue(any(f"{self.label} login required: please log in" in line for line in logs.output))
        self.assertEqual(FakeSession.instances[0].events, [("login", "https://feed"), "closed"])

    def test_row_failure_recovers_and_continues(self):
        for err in (RuntimeError("composer stuck"), PWTimeoutError("timed out")):
            with self.subTest(err=type(err).__name__):
                self.schedule.side_effect = [err, "20260106:ok"]
                with self.assertLogs(self.module.logger, level="ERROR") as logs:
                    out = self._run(_rows("20260105", "20260106"))
                self.assertEqual(out[0], {"day": "20260105", "status": "FAIL",
                                          "detail": f"{err} (screenshot 20260105-{self.slug}-video-error.png)"})
                self.assertEqual(out[1]["status"], "LIVE")
                self.assertTrue(any(f"{self.label} 20260105 failed" in line for line in logs.output))
                events = FakeSession.instances[-1].events
                first = events.index(("home", "https://feed"))
                self.assertEqual(events[first:first + 3], [("home", "https://feed"), "cancel", ("home", "https://feed")])

    def test_other_errors_propagate(self):
        self.schedule.side_effect = ValueError("bug")
        with self.assertRaises(ValueError):
            self._run(_rows("20260105"))


class ThreadsDriverTests(_DriverCase, unittest.TestCase):
    module = videos_threads
    session_attr, loader_attr, home_attr = "ThreadsSession", "load_threads_config", "return_to_profile"
    slug, label, settle_ms = "th", "TH", (3500,)


class TwitterDriverTests(_DriverCase, unittest.TestCase):
    module = videos_twitter
    session_attr, loader_attr, home_attr = "TwitterSession", "load_twitter_config", "return_to_home"
    slug, label, settle_ms, modal_attr = "tw", "TW", (3500, 400), "dismiss_blocking_modals"


class InstagramDriverTests(_DriverCase, unittest.TestCase):
    module = videos_instagram
    session_attr, loader_attr, home_attr = "InstagramSession", "load_instagram_config", "return_to_planner"
    slug, label, settle_ms, modal_attr = "ig", "IG", (4500, 500), "dismiss_meta_verified_modal"


if __name__ == "__main__":
    unittest.main()
