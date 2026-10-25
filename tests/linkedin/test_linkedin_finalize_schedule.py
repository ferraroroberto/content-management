"""One LinkedIn schedule tail for the photo, carousel and video flows (issue #371).

``linkedin_composer.finalize_schedule`` is the Confirm → final Schedule →
confirmed tail every LinkedIn route ends on. The video driver used to keep
its own copy and import eight private helpers from the photo scheduler.

Each test drives a caller's real entry point (``schedule_one_illustration_row``,
``schedule_one_carousel_row``, ``schedule_one_video``) with only the steps
*before* the Schedule dialog stubbed. The tail runs for real against a fake
page that answers the Playwright calls it makes. Stubs are looked up by
whichever spelling the module defines, so the same tests run on the pre-#371
copies and on the shared function.

* ``EquivalenceTests`` pass on both: click order, status strings, the
  background-upload wait only for media routes, the FAIL screenshot names, and
  the late-remounting composer from issue #319.
* ``DriftTests`` pin where the video copy differed, resolved to the photo
  flow's safer side (they fail on the old copy): an error inside the
  post-schedule upload wait is logged instead of failing a row that is already
  scheduled (a FAIL row stays Work-in-Progress and is scheduled again on the
  next run, issue #178), and the video tail now also saves the ``-pre`` /
  ``-after`` screenshots.

No browser, no network, no LinkedIn: time only moves inside the fake page's
``wait_for_timeout``.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/linkedin/test_linkedin_finalize_schedule.py -q
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from planning.linkedin import schedule_linkedin_posts as li
from planning.linkedin.linkedin_labels import CONFIRM_BTN_RE, FINAL_SCHEDULE_BTN_RE
from planning.videos import videos_linkedin as vl
from planning.videos.videos_session import ClipPayload, VideoRow

DAY = date(2026, 9, 29)
DAY_TITLE = "20260929"
UPLOAD_FALLBACK_MS = 60000  # wait_for_upload_complete's no-indicator safety hold


class _Composer:
    """Composer absent for the first ``absent_polls`` counts, then open until scheduled."""

    def __init__(self, absent_polls: int = 0):
        self._absent = absent_polls
        self.scheduled = False

    def count(self) -> int:
        if self.scheduled:
            return 0
        if self._absent:
            self._absent -= 1
            return 0
        return 1


class _Nothing:
    """A locator that matches nothing (toasts, upload indicators)."""

    first = property(lambda self: self)

    def count(self) -> int:
        return 0


class _Button:
    def __init__(self, page: "_FakePage", name):
        self._page, self._name = page, name

    first = property(lambda self: self)
    last = property(lambda self: self)

    def click(self, **_kwargs) -> None:
        self._page.clicks.append(self._name)
        if self._name is FINAL_SCHEDULE_BTN_RE:
            self._page.composer.scheduled = True


class _Locator(_Nothing):
    def __init__(self, page: "_FakePage"):
        self._page = page

    def get_by_role(self, _role: str, *, name) -> _Button:
        return _Button(self._page, name)

    def filter(self, **_kwargs) -> _Composer:
        return self._page.composer


class _FakePage:
    """Virtual-clock page: time only moves inside ``wait_for_timeout``."""

    def __init__(self, composer: _Composer, *, crash_upload_wait: bool = False):
        self.composer = composer
        self.crash_upload_wait = crash_upload_wait
        self.now = 0
        self.waits: list[int] = []
        self.clicks: list = []
        self.shots: list[str] = []

    def evaluate(self, _expr: str) -> int:
        return self.now

    def wait_for_timeout(self, ms: int) -> None:
        # The upload wait's first act is a 2 s settle; nothing else sleeps 2 s
        # once the post is scheduled. A closed page raises exactly there.
        if self.crash_upload_wait and self.composer.scheduled and ms == 2000:
            raise RuntimeError("Target page, context or browser has been closed")
        self.waits.append(ms)
        self.now += ms

    def screenshot(self, *, path: str, **_kwargs) -> None:
        self.shots.append(Path(path).name)

    def get_by_text(self, _pattern) -> _Nothing:
        return _Nothing()

    def locator(self, _selector: str) -> _Locator:
        return _Locator(self)


def _stub_steps(stack: contextlib.ExitStack, module, steps) -> None:
    """Stub each pre-Schedule step under whichever spelling ``module`` defines."""
    for spellings in steps:
        spellings = (spellings,) if isinstance(spellings, str) else spellings
        name = next(n for n in spellings if hasattr(module, n))
        stack.enter_context(mock.patch.object(module, name))


SCHEDULE_DIALOG = (("_open_schedule_dialog", "open_schedule_dialog"),
                   ("_set_schedule_datetime", "set_schedule_datetime"))
PHOTO_STEPS = ("_click_add_photo", "_upload_photo", "_set_alt_text",
               "_click_next_after_photo_editor", "_fill_caption") + SCHEDULE_DIALOG
CAROUSEL_STEPS = ("_click_start_a_post", "_click_more", "_click_add_a_document",
                  "_share_document_choose_file", "_fill_document_title",
                  "_wait_for_pdf_upload", "_click_document_done",
                  "fill_caption_with_mentions") + SCHEDULE_DIALOG
VIDEO_STEPS = ("_click_video_button", "_upload_video", "_wait_for_video_ready",
               "_click_video_next", "_fill_caption_with_mentions") + SCHEDULE_DIALOG


def _li_row() -> li.ScheduleRow:
    return li.ScheduleRow("row", DAY, "ILL", "ill", None, 0, 0, None)


def _run_photo(page: _FakePage) -> str:
    with contextlib.ExitStack() as stack:
        _stub_steps(stack, li, PHOTO_STEPS)
        stack.enter_context(mock.patch.object(li, "_resolve_schedule_time", return_value=(6, 30)))
        return li.schedule_one_illustration_row(
            mock.Mock(page=page), {"feed_url": "https://example.invalid/feed"}, _li_row(),
            li.IllustrationData("a.png", "alt", "caption"), Path("a.png"), dry_run=False,
        )


def _run_carousel(page: _FakePage) -> str:
    with contextlib.ExitStack() as stack:
        _stub_steps(stack, li, CAROUSEL_STEPS)
        stack.enter_context(mock.patch.object(li, "_resolve_schedule_time", return_value=(6, 30)))
        return li.schedule_one_carousel_row(
            mock.Mock(page=page), {"feed_url": "https://example.invalid/feed"}, _li_row(),
            mock.Mock(pdf_path=Path("doc.pdf"), doc_title="doc"), "caption", dry_run=False,
        )


def _run_video(page: _FakePage) -> str:
    payload = ClipPayload(
        title="clip", video_path=Path("clip.mp4"),
        caption_short="short", caption_long="long caption",
    )
    row = VideoRow(page_id="row", day=DAY, payload=payload, existing_post_url=None)
    with contextlib.ExitStack() as stack:
        _stub_steps(stack, vl, VIDEO_STEPS)
        return vl.schedule_one_video(
            mock.Mock(page=page), {"feed_url": "https://example.invalid/feed"},
            {"post_hour_local": 19, "post_minute_local": 0}, row, dry_run=False,
        )


class EquivalenceTests(unittest.TestCase):
    """Same behaviour on the pre-#371 copies and on ``finalize_schedule``."""

    def test_photo_route(self) -> None:
        page = _FakePage(_Composer())
        self.assertEqual(_run_photo(page), f"{DAY_TITLE}: LIVE scheduled")
        self.assertEqual(page.clicks, [CONFIRM_BTN_RE, FINAL_SCHEDULE_BTN_RE])
        self.assertNotIn(UPLOAD_FALLBACK_MS, page.waits)
        self.assertEqual(page.shots, [f"{DAY_TITLE}-live-pre.png", f"{DAY_TITLE}-live-after.png"])

    def test_carousel_route_waits_for_the_upload(self) -> None:
        page = _FakePage(_Composer())
        self.assertEqual(_run_carousel(page), f"{DAY_TITLE}: LIVE scheduled")
        self.assertEqual(page.clicks, [CONFIRM_BTN_RE, FINAL_SCHEDULE_BTN_RE])
        self.assertIn(UPLOAD_FALLBACK_MS, page.waits)

    def test_carousel_upload_wait_error_keeps_the_row_live(self) -> None:
        page = _FakePage(_Composer(), crash_upload_wait=True)
        self.assertEqual(_run_carousel(page), f"{DAY_TITLE}: LIVE scheduled")

    def test_video_route_waits_for_the_upload(self) -> None:
        page = _FakePage(_Composer())
        self.assertEqual(_run_video(page), "LI:LIVE")
        self.assertEqual(page.clicks, [CONFIRM_BTN_RE, FINAL_SCHEDULE_BTN_RE])
        self.assertIn(UPLOAD_FALLBACK_MS, page.waits)

    def test_video_late_remounting_composer_is_confirmed_live(self) -> None:
        """Issue #319: the composer re-mounts after Confirm; no toast ever shows."""
        self.assertEqual(_run_video(_FakePage(_Composer(absent_polls=4))), "LI:LIVE")

    def test_unconfirmed_schedule_fails_with_the_route_screenshot(self) -> None:
        """No positive evidence stays a FAIL — never inferred as success."""
        for run, shot in ((_run_photo, f"{DAY_TITLE}-live-FAIL.png"),
                          (_run_carousel, f"{DAY_TITLE}-live-FAIL.png"),
                          (_run_video, f"{DAY_TITLE}-li-FAIL.png")):
            with self.subTest(route=run.__name__):
                page = _FakePage(_Composer(absent_polls=10**6))
                with self.assertRaises(RuntimeError) as ctx:
                    run(page)
                self.assertIn("MAY still have been scheduled", str(ctx.exception))
                self.assertIn(shot, str(ctx.exception))
                self.assertEqual(page.shots[-1], shot)


class DriftTests(unittest.TestCase):
    """Where the video copy differed; the safer side wins (fails on the old copy)."""

    def test_video_upload_wait_error_keeps_the_row_live(self) -> None:
        page = _FakePage(_Composer(), crash_upload_wait=True)
        self.assertEqual(_run_video(page), "LI:LIVE")

    def test_video_saves_pre_and_after_screenshots(self) -> None:
        page = _FakePage(_Composer())
        _run_video(page)
        self.assertEqual(page.shots, [f"{DAY_TITLE}-li-pre.png", f"{DAY_TITLE}-li-after.png"])

    def test_video_driver_imports_nothing_from_the_photo_scheduler(self) -> None:
        tree = ast.parse(inspect.getsource(vl))
        sources = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("planning.linkedin.schedule_linkedin_posts", sources)


if __name__ == "__main__":
    unittest.main()
