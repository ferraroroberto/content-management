"""One planned-post coverage rule judges every platform's missing post metrics.

``check_posts_coverage`` flags a platform whose consolidated ``posts`` row has no
post id for the covered day, then asks the editorial calendar whether a post was
planned for it: planned means a failure naming the platform and the day, not
planned means a non-failing note, and a calendar that cannot be read means the
check is not confirmed. Only the Notion boundary is mocked; the fixtures are
synthetic and no network is touched.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import reporting_pipeline as rp

COLUMNS = {"title_day": "day", "caption_text": "synthetic", "text_body": "synthetic"}
BLOCK = {"editorial_db_id": "db", "editorial_columns": COLUMNS, "notion_columns": COLUMNS}
COVERED_DAY = "20261109"
PROCESSING_DATE = "2026-11-10"


class _FakeCursor:
    def __init__(self, columns: list[str], row: tuple):
        self.description = [(c,) for c in columns]
        self._row = row

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


def row_missing_only(platform: str | None) -> tuple[list[str], tuple]:
    """A ``posts`` row with every platform's post id set except ``platform``'s."""
    columns, values = ["date"], [PROCESSING_DATE]
    for p in rp.COVERAGE_PLATFORMS:
        for suffix in ("no_video", "video"):
            columns.append(f"post_id_{p}_{suffix}")
            values.append(None if p == platform else f"https://example.test/{p}/{suffix}")
    return columns, tuple(values)


class _CoverageCase(unittest.TestCase):
    def check(self, platform: str | None, *, row=None, text=None, client="client", read_error=None):
        """Run the coverage check with ``platform`` missing and the editorial row as given."""
        patches = [
            patch(
                "reporting.process.supabase_uploader.get_db_connection",
                return_value=_FakeConnection(*row_missing_only(platform)),
            ),
            patch.object(rp, "load_block", return_value=BLOCK),
            patch.object(rp, "load_full_config", return_value={"notion": {"api_token": "token"}}),
            patch("reporting.notion.editorial.init_notion_client", return_value=client),
            patch("reporting.notion.editorial.get_row_by_day", side_effect=read_error, return_value=row),
            patch("reporting.notion.editorial.get_field", return_value=text),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        rp.logger = MagicMock()
        failures = rp.PipelineFailures()
        rp.check_posts_coverage(PROCESSING_DATE, failures)
        return failures


class EveryPlatformSameRuleTests(_CoverageCase):
    def test_planned_but_missing_fails_naming_platform_and_day(self):
        for platform in rp.COVERAGE_PLATFORMS:
            with self.subTest(platform=platform):
                failures = self.check(platform, row={"id": "r"}, text="synthetic caption")

                self.assertEqual(failures.missing_post_metrics, [f"{platform} (planned for {COVERED_DAY})"])
                self.assertEqual(failures.nothing_planned, [])
                self.assertTrue(failures.any())

    def test_not_planned_is_a_note_not_a_failure(self):
        for platform in rp.COVERAGE_PLATFORMS:
            for state in ({"row": None}, {"row": {"id": "r"}, "text": "   "}):
                with self.subTest(platform=platform, state=state):
                    failures = self.check(platform, **state)

                    self.assertEqual(failures.nothing_planned, [f"{platform} (nothing planned for {COVERED_DAY})"])
                    self.assertEqual(failures.missing_post_metrics, [])
                    self.assertFalse(failures.any())
                    rp.logger.error.assert_not_called()
                    self.assertTrue(rp.logger.warning.called)

    def test_unreadable_planning_state_is_not_confirmed(self):
        unreadable = (
            {"row": None, "client": None},
            {"row": None, "read_error": RuntimeError("unreachable")},
            {"row": {"id": "r"}, "text": None},
        )
        for platform in rp.COVERAGE_PLATFORMS:
            for state in unreadable:
                with self.subTest(platform=platform, state=state):
                    failures = self.check(platform, **state)

                    self.assertEqual(failures.nothing_planned, [])
                    self.assertEqual(failures.missing_post_metrics, [])
                    self.assertEqual(len(failures.unverified), 1)
                    self.assertIn(platform, failures.unverified[0])
                    self.assertTrue(failures.any())

    def test_present_metrics_pass_without_asking_the_calendar(self):
        with patch.object(rp, "_planned_post") as planned:
            failures = self.check(None)

        planned.assert_not_called()
        self.assertFalse(failures.any())
        self.assertEqual(failures.nothing_planned, [])

    def test_skipped_source_platform_is_not_judged_again(self):
        with patch.object(rp, "_planned_post") as planned:
            failures = rp.PipelineFailures()
            failures.skipped_platforms.append("threads (profile + posts; upstream unavailable)")
            with patch(
                "reporting.process.supabase_uploader.get_db_connection",
                return_value=_FakeConnection(*row_missing_only("threads")),
            ):
                rp.logger = MagicMock()
                rp.check_posts_coverage(PROCESSING_DATE, failures)

        planned.assert_not_called()
        self.assertEqual(failures.missing_post_metrics, [])


class AlertMessageTests(unittest.TestCase):
    def test_nothing_planned_is_listed_apart_from_planned_but_missing(self):
        failures = rp.PipelineFailures()
        failures.missing_post_metrics.append("threads (planned for 20261109)")
        failures.nothing_planned.append("twitter (nothing planned for 20261109)")

        message = rp._build_alert_message(failures, PROCESSING_DATE)

        planned_at = message.index("• threads (planned for 20261109)")
        nothing_at = message.index("• twitter (nothing planned for 20261109)")
        self.assertIn("Planned but no post metrics (platform):", message[:planned_at])
        self.assertIn("Nothing planned", message[planned_at:nothing_at])
        self.assertIn("finished with failures", message)

    def test_a_week_with_nothing_planned_alone_is_not_headed_a_failure(self):
        failures = rp.PipelineFailures()
        failures.nothing_planned.append("twitter (nothing planned for 20261109)")

        message = rp._build_alert_message(failures, PROCESSING_DATE)

        self.assertFalse(failures.any())
        self.assertNotIn("failures", message)
        self.assertIn("• twitter (nothing planned for 20261109)", message)


class RunEndTests(unittest.TestCase):
    def test_nothing_planned_alone_sends_the_note_and_exits_green(self):
        failures = rp.PipelineFailures()
        failures.nothing_planned.append("twitter (nothing planned for 20261109)")
        rp.logger = MagicMock()

        with patch.object(rp, "send_failure_alert") as send:
            rp._report_outcome(failures, PROCESSING_DATE)

        send.assert_called_once_with(failures, PROCESSING_DATE)
        rp.logger.error.assert_not_called()

    def test_a_clean_run_sends_nothing(self):
        rp.logger = MagicMock()

        with patch.object(rp, "send_failure_alert") as send:
            rp._report_outcome(rp.PipelineFailures(), PROCESSING_DATE)

        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
