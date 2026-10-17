r"""Characterization test for ``videos_instagram.schedule_one_video``'s reel-composer call order (issue #409).

The Instagram video driver walks the Reels wizard through eight composer
helpers imported from ``planning.instagram.instagram_reels``. These pin the
sequence and arguments for the dry-run and live paths, so moving/renaming those
helpers (private -> public) cannot change what the driver does.

No browser: every helper is a recorder, the page is a mock.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from planning.videos import videos_instagram as vi

HELPERS = (
    "click_reel_footer",
    "dismiss_reel_success_dialog",
    "reel_add_video",
    "select_reel_schedule_option",
    "set_reel_schedule_datetime",
    "wait_reel_composer_closes",
    "wait_reel_footer_enabled",
    "wait_reels_composer_ready",
    "open_day_schedule_menu",
    "fill_post_text",
    "cancel_composer",
)


class ReelFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple] = []
        for name in HELPERS:
            rec = mock.Mock(side_effect=lambda *a, _n=name, **k: self.calls.append((_n, a[1:], k)))
            patcher = mock.patch.object(vi, name, rec)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.closes = mock.patch.object(
            vi, "wait_reel_composer_closes", return_value=True
        ).start()
        self.addCleanup(mock.patch.stopall)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mock.patch.object(vi, "results_dir", return_value=Path(tmp.name)).start()
        self.session = SimpleNamespace(page=mock.Mock(name="page"))
        self.row = SimpleNamespace(
            day_title="20260105",
            day=date(2026, 1, 5),
            payload=SimpleNamespace(video_path=Path("clip.mp4"), caption_short="cap"),
        )
        self.cfg = {"post_hour_local": 9, "post_minute_local": 30}

    def _run(self, dry_run: bool) -> str:
        return vi.schedule_one_video(self.session, {}, self.cfg, self.row, dry_run=dry_run)

    def test_dry_run_sets_up_the_reel_then_cancels(self) -> None:
        self.assertEqual(self._run(True), "IG:DRY")
        self.assertEqual(
            [c[0] for c in self.calls],
            [
                "open_day_schedule_menu", "wait_reels_composer_ready", "reel_add_video",
                "fill_post_text", "wait_reel_footer_enabled", "click_reel_footer",
                "select_reel_schedule_option", "set_reel_schedule_datetime", "cancel_composer",
            ],
        )

    def test_live_path_clicks_schedule_and_dismisses_the_dialog(self) -> None:
        self.closes.return_value = True
        self.assertEqual(self._run(False), "IG:LIVE")
        names = [c[0] for c in self.calls]
        self.assertEqual(
            names[-2:], ["click_reel_footer", "dismiss_reel_success_dialog"]
        )
        footer = [c for c in self.calls if c[0] == "click_reel_footer"]
        self.assertEqual([c[1] for c in footer], [("Next",), ("Schedule",)])
        enabled = [c for c in self.calls if c[0] == "wait_reel_footer_enabled"]
        self.assertEqual([(c[1], c[2]) for c in enabled],
                         [(("Next",), {"timeout_ms": 120000}), (("Schedule",), {"timeout_ms": 120000})])
        when = next(c for c in self.calls if c[0] == "set_reel_schedule_datetime")
        self.assertEqual(when[1], (date(2026, 1, 5), 9, 30))

    def test_composer_that_never_closes_raises(self) -> None:
        self.closes.return_value = False
        with self.assertRaises(RuntimeError):
            self._run(False)


if __name__ == "__main__":
    unittest.main()
