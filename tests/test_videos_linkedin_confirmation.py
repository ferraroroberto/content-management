"""Regression test for the LinkedIn video confirmation false negative (issue #319).

A live video row was scheduled but reported FAIL: the pre-click snapshot ran
before LinkedIn had re-mounted the composer after the Schedule dialog's
Confirm, so it matched zero composers and only the "Post scheduled" toast
could confirm — which, for a video still processing, arrived after the 45 s
wait. The row stayed Work-in-Progress with an empty ``LI(v)`` link, queued for
a duplicate on the next run.

This drives ``videos_linkedin.schedule_one_video`` itself with every UI step
stubbed, against a fake page whose composer re-mounts late and closes on the
final click, and never shows the toast.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from planning.videos import videos_linkedin
from planning.videos.videos_session import ClipPayload, VideoRow


class _Toast:
    def count(self) -> int:
        return 0


class _FakePage:
    """Virtual-clock page: time only moves inside ``wait_for_timeout``."""

    def __init__(self):
        self._now = 0

    def evaluate(self, _expr: str) -> int:
        return self._now

    def wait_for_timeout(self, ms: int) -> None:
        self._now += ms

    def get_by_text(self, _pattern) -> _Toast:
        return _Toast()

    def screenshot(self, **_kwargs) -> None:
        pass


class _LateComposer:
    """Composer absent for the first ``absent_polls`` counts, then open until clicked."""

    def __init__(self, absent_polls: int):
        self._absent = absent_polls
        self.scheduled = False

    def count(self) -> int:
        if self.scheduled:
            return 0
        if self._absent:
            self._absent -= 1
            return 0
        return 1


def _row() -> VideoRow:
    payload = ClipPayload(
        clip_page_id="clip",
        title="clip",
        video_path=Path("clip.mp4"),
        thumb_path=Path("clip.png"),
        caption_short="short",
        caption_long="long caption",
    )
    return VideoRow(page_id="row", day=date(2026, 9, 29), payload=payload, existing_post_url=None)


class VideoScheduleConfirmationTests(unittest.TestCase):

    def _run(self, composer: _LateComposer) -> str:
        page = _FakePage()
        session = mock.Mock(page=page)

        def final_click(_page):
            composer.scheduled = True

        stubs = {
            name: mock.DEFAULT for name in (
                "_click_video_button", "_upload_video", "_wait_for_video_ready",
                "_click_video_next", "_fill_caption_with_mentions",
                "_open_schedule_dialog", "_set_schedule_datetime",
                "_click_schedule_confirm", "_wait_for_upload_complete",
            )
        }
        with mock.patch.multiple(videos_linkedin, **stubs), \
                mock.patch.object(videos_linkedin, "_composer_dialog", return_value=composer), \
                mock.patch.object(videos_linkedin, "_click_final_schedule", side_effect=final_click):
            return videos_linkedin.schedule_one_video(
                session, {"feed_url": "https://example.invalid/feed"},
                {"post_hour_local": 19, "post_minute_local": 0}, _row(), dry_run=False,
            )

    def test_late_remounting_composer_is_confirmed_live(self):
        """The composer comes back after the snapshot used to run; no toast ever shows."""
        self.assertEqual(self._run(_LateComposer(absent_polls=4)), "LI:LIVE")

    def test_composer_that_never_appears_still_fails(self):
        """No positive evidence stays a FAIL — the fix must not infer success."""
        composer = _LateComposer(absent_polls=10**6)
        with self.assertRaises(RuntimeError) as ctx:
            self._run(composer)
        self.assertIn("MAY still have been scheduled", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
