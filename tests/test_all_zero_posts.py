"""A platform whose scraped posts are all 0 likes / 0 comments / 0 reposts fails the run (issue #478).

LinkedIn changed its markup on 2026-10-15 and the scrape kept saving zeros for ten days
while the Job log stayed green. ``check_all_zero_posts`` names such a platform in
``PipelineFailures``; the run lets every platform finish, then ``main()`` exits non-zero,
which is what app-launcher's ``alert_on_failure`` reacts to.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_all_zero_posts -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import reporting_pipeline as rp

DATE = "2026-10-25"
CONFIG = {
    "linkedin_posts": {"api_url": "x"},
    "linkedin_profile": {"api_url": "x"},
    "twitter_posts": {"api_url": "x"},
    "instagram_posts": {"api_url": "x"},
}


def _post(likes: int, comments: int, reshares: int) -> dict:
    return {
        "post_id": "p", "posted_at": DATE, "is_video": 0,
        "num_likes": likes, "num_comments": comments, "num_reshares": reshares,
    }


class AllZeroPostsTest(unittest.TestCase):
    def setUp(self) -> None:
        rp.logger = MagicMock()
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.files: dict[str, Path] = {}
        # linkedin: every post zero. twitter: one real count among zeros. instagram: no file at all.
        self._write(tmp, "linkedin", [_post(0, 0, 0), _post(0, 0, 0)])
        self._write(tmp, "twitter", [_post(0, 0, 0), _post(3, 0, 0)])
        self.enterContext(patch.object(rp, "check_file_exists_for_date", self._exists))

    def _write(self, tmp: Path, platform: str, posts: list[dict]) -> None:
        path = tmp / f"{platform}_posts_{DATE}.json"
        path.write_text(
            json.dumps({"date": DATE, "platform": platform, "data_type": "posts", "data": {"posts": posts}}),
            encoding="utf-8",
        )
        self.files[f"{platform}_posts"] = path

    def _exists(self, platform_key: str, _config: dict, _date: str) -> tuple[bool, str]:
        path = self.files.get(platform_key)
        return (path is not None, str(path))

    def test_only_the_all_zero_platform_is_recorded(self) -> None:
        failures = rp.PipelineFailures()
        rp.check_all_zero_posts(CONFIG, DATE, failures)
        self.assertEqual(failures.all_zero_platforms, ["linkedin"])
        self.assertTrue(failures.any())
        self.assertEqual(failures.step_failures, [])

    def test_alert_message_names_the_platform(self) -> None:
        failures = rp.PipelineFailures()
        rp.check_all_zero_posts(CONFIG, DATE, failures)
        self.assertIn("• linkedin", rp._build_alert_message(failures, DATE))

    def test_run_finishes_every_platform_then_exits_non_zero(self) -> None:
        # Only the API fetch is wired to a real step; the rest are skipped so the run stays offline.
        fetch = MagicMock(return_value=None)
        argv = ["reporting_pipeline.py", "-p", "-a", "-c", "-n", "-b", "--date", DATE]
        with patch.object(sys, "argv", argv), \
                patch.object(rp, "_load_config", return_value=CONFIG), \
                patch.object(rp, "run_social_api_client", fetch), \
                patch.object(rp, "check_endpoint_coverage"), \
                patch.object(rp, "check_policy_drift_coverage"), \
                patch.object(rp, "send_failure_alert") as alert, \
                self.assertRaises(SystemExit) as ctx:
            rp.main()
        self.assertEqual(ctx.exception.code, 1)
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0].all_zero_platforms, ["linkedin"])

    def test_no_all_zero_platform_is_a_clean_run(self) -> None:
        self.files["linkedin_posts"].write_text(
            json.dumps({"date": DATE, "platform": "linkedin", "data_type": "posts",
                        "data": {"posts": [_post(0, 0, 0), _post(0, 4, 0)]}}),
            encoding="utf-8",
        )
        failures = rp.PipelineFailures()
        rp.check_all_zero_posts(CONFIG, DATE, failures)
        self.assertEqual(failures.all_zero_platforms, [])
        self.assertFalse(failures.any())


if __name__ == "__main__":
    unittest.main()
