"""Regression test for issue #321 finding 5: the orchestrator's failure
detection counted only `FAIL` / `LOGIN-REQUIRED` rows or a platform-level
`error`, ignoring each platform's own non-zero `exit_code` — so a live Videos
row that ended `PARTIAL` (LI live, IG failed), which `schedule_videos_posts`
itself counts as a failure, still yielded pipeline exit 0 and verdict `clean`.

The duplicated computation (markdown verdict + exit code) is now one shared
helper (`_platform_failed` / `_any_platform_failed`); this pins both call
sites can no longer drift apart.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning_pipeline import PlatformResult, _any_platform_failed, _platform_failed


class PlatformFailedTests(unittest.TestCase):
    def test_partial_row_status_counts_as_failed(self):
        r = PlatformResult(name="Videos", exit_code=0, rows=[
            {"day": "Mon", "status": "PARTIAL", "detail": "LI:LIVE, IG:FAIL"},
        ])
        self.assertTrue(_platform_failed(r))

    def test_nonzero_exit_code_counts_as_failed_even_with_clean_rows(self):
        r = PlatformResult(name="Videos", exit_code=11, rows=[
            {"day": "Mon", "status": "LIVE", "detail": "all live"},
        ])
        self.assertTrue(_platform_failed(r))

    def test_skipped_platform_never_fails(self):
        r = PlatformResult(name="Twitter", skipped=True, exit_code=11, rows=[
            {"day": "Mon", "status": "FAIL", "detail": "would have failed"},
        ])
        self.assertFalse(_platform_failed(r))

    def test_clean_live_platform_does_not_fail(self):
        r = PlatformResult(name="LinkedIn", exit_code=0, rows=[
            {"day": "Mon", "status": "LIVE", "detail": ""},
            {"day": "Tue", "status": "DRY", "detail": ""},
        ])
        self.assertFalse(_platform_failed(r))

    def test_any_platform_failed_true_when_one_of_several_fails(self):
        clean = PlatformResult(name="LinkedIn", exit_code=0, rows=[
            {"day": "Mon", "status": "LIVE", "detail": ""},
        ])
        partial_videos = PlatformResult(name="Videos", exit_code=11, rows=[
            {"day": "Mon", "status": "PARTIAL", "detail": "LI:LIVE, IG:FAIL"},
        ])
        self.assertTrue(_any_platform_failed([clean, partial_videos]))

    def test_any_platform_failed_false_when_all_clean_or_skipped(self):
        clean = PlatformResult(name="LinkedIn", exit_code=0, rows=[
            {"day": "Mon", "status": "LIVE", "detail": ""},
        ])
        skipped = PlatformResult(name="Twitter", skipped=True)
        self.assertFalse(_any_platform_failed([clean, skipped]))


if __name__ == "__main__":
    unittest.main()
