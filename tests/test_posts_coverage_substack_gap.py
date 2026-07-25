"""Regression test: substack coverage gaps with no editorial content queued
must be an alert, not a failure (issue #182).

``check_posts_coverage`` (issue #84) flags any platform in ``COVERAGE_PLATFORMS``
whose consolidated ``posts`` row has no ``post_id`` for the date. Before this
fix, that included days where substack's editorial calendar genuinely had no
Note queued — a normal, expected state (``post_substack_note.py`` treats a
missing editorial row as a no-op, not an error) — which nonetheless logged an
``❌`` error, appended to ``failures.missing_post_metrics``, fired the Slack
failure alert, and exited the pipeline with code 1.

This reproduces both branches against a mocked DB row with every platform
present except substack:

* editorial content genuinely absent for the covered day -> warning only,
  substack excluded from ``failures.missing_post_metrics``.
* editorial content was queued (or unverifiable) -> unchanged legacy
  behavior: error logged, substack recorded as a failure.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import reporting_pipeline as rp


def _row_missing_only(platform: str) -> tuple[list[str], tuple]:
    """A consolidated ``posts`` row with every ``COVERAGE_PLATFORMS`` post_id
    populated except the given platform's (both video and non-video)."""
    columns = ["date"]
    values = ["2026-07-25"]
    for p in rp.COVERAGE_PLATFORMS:
        for suffix in ("no_video", "video"):
            columns.append(f"post_id_{p}_{suffix}")
            values.append(None if p == platform else f"https://example.com/{p}/{suffix}")
    return columns, tuple(values)


class _FakeCursor:
    def __init__(self, columns: list[str], row: tuple):
        self._columns = columns
        self._row = row
        self.description = [(c,) for c in columns]

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeConnection:
    def __init__(self, columns: list[str], row: tuple):
        self._columns = columns
        self._row = row

    def cursor(self):
        return _FakeCursor(self._columns, self._row)

    def close(self):
        pass


class PostsCoverageSubstackGapTests(unittest.TestCase):
    def setUp(self):
        columns, row = _row_missing_only("substack")
        self.connection_patch = patch(
            "reporting.process.supabase_uploader.get_db_connection",
            return_value=_FakeConnection(columns, row),
        )
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        rp.logger = MagicMock()

    def test_no_editorial_content_is_alert_not_failure(self):
        """Genuinely empty editorial day: warning only, not a recorded failure."""
        with patch.object(rp, "_substack_had_editorial_content", return_value=False):
            failures = rp.PipelineFailures()
            rp.check_posts_coverage("2026-07-25", failures)

        self.assertNotIn("substack", failures.missing_post_metrics)
        self.assertFalse(failures.any())
        self.assertTrue(
            any("expected, not a failure" in call.args[0] for call in rp.logger.warning.call_args_list),
            f"expected a warning noting the gap is expected; got: {rp.logger.warning.call_args_list}",
        )
        rp.logger.error.assert_not_called()

    def test_editorial_content_was_queued_still_fails(self):
        """Content WAS queued (or can't be verified) — unchanged hard-failure path."""
        with patch.object(rp, "_substack_had_editorial_content", return_value=True):
            failures = rp.PipelineFailures()
            rp.check_posts_coverage("2026-07-25", failures)

        self.assertIn("substack", failures.missing_post_metrics)
        self.assertTrue(failures.any())
        self.assertTrue(
            any("no post metrics for substack" in call.args[0] for call in rp.logger.error.call_args_list),
            f"expected the existing error log; got: {rp.logger.error.call_args_list}",
        )

    def test_unverifiable_editorial_check_still_fails(self):
        """Notion unreachable (None) — fall back to the safer hard-failure path."""
        with patch.object(rp, "_substack_had_editorial_content", return_value=None):
            failures = rp.PipelineFailures()
            rp.check_posts_coverage("2026-07-25", failures)

        self.assertIn("substack", failures.missing_post_metrics)
        self.assertTrue(failures.any())


if __name__ == "__main__":
    unittest.main()
