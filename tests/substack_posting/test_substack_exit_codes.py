"""A Note that posted without its link surfaces as its own named condition (issue #371).

``publish_note_flow`` returns ``RC_POSTED_LINK_NOT_WRITTEN`` (11) when the
Note is live but its Notion link column could not be written. These tests
follow that code out of the Substack daily pipeline and into the reporting
pipeline's failure list, which feeds the Telegram alert, and check that it is
named there: distinct from rc 99 (a Note branch raised) and from a failed
post (any other non-zero code, recorded by number only).

Both Note publishers, the config, the policy check and the alert are fakes.
No browser, no network, no Notion.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/substack_posting/test_substack_exit_codes.py -q
"""

from __future__ import annotations

import argparse
import contextlib
import unittest
from unittest import mock

import reporting_pipeline as rp
from planning.substack import daily_pipeline as dp
from planning.substack import post_substack_note as sn

LINK_NOT_WRITTEN = getattr(sn, "RC_POSTED_LINK_NOT_WRITTEN", 11)
DAY = "20260929"


def run_daily(image_rc=0, video_rc=None, image_err=None) -> tuple[int, list[str]]:
    """Run ``daily_pipeline.main`` with fake Note branches; return (rc, log lines)."""
    args = argparse.Namespace(date=DAY, dry_run=False, skip_post=False, force=False, debug=False)
    with contextlib.ExitStack() as stack:
        for name, kwargs in {
            "parse_args": {"return_value": args},
            "configure_logger": {},
            "load_substack_config": {"return_value": {"note_source": "native"}},
            "post_note": {"return_value": image_rc, "side_effect": image_err},
            "post_video_note_if_applicable": {"return_value": video_rc},
        }.items():
            stack.enter_context(mock.patch.object(dp, name, **kwargs))
        logs = stack.enter_context(unittest.TestCase().assertLogs("substack_daily_pipeline", "INFO"))
        rc = dp.main()
    return rc, logs.output


def record(rc: int) -> list[tuple[str, str]]:
    """The step failures ``run_pipeline`` records when the Substack step returns ``rc``."""
    with contextlib.ExitStack() as stack:
        for name, kwargs in {
            "logger": {},
            "configure_logger": {},
            "_load_config": {"return_value": None},
            "run_substack_daily_pipeline": {"return_value": rc},
            "check_policy_drift_coverage": {},
            "send_failure_alert": {},
        }.items():
            stack.enter_context(mock.patch.object(rp, name, mock.MagicMock(**kwargs)))
        failures = rp.run_pipeline(
            skip_api=True, skip_processing=True, skip_aggregation=True,
            skip_consolidation=True, skip_notion=True, reference_date=DAY,
        )
    return failures.step_failures


class DailyPipelineTests(unittest.TestCase):
    def test_image_note_link_not_written_is_the_pipeline_result(self) -> None:
        rc, logs = run_daily(image_rc=LINK_NOT_WRITTEN)
        self.assertEqual(rc, LINK_NOT_WRITTEN)
        self.assertIn(f"post={LINK_NOT_WRITTEN} (Note posted, link not written", logs[-1])

    def test_video_note_link_not_written_is_the_pipeline_result(self) -> None:
        rc, logs = run_daily(image_rc=0, video_rc=LINK_NOT_WRITTEN)
        self.assertEqual(rc, LINK_NOT_WRITTEN)
        self.assertIn(f"post={LINK_NOT_WRITTEN} (Note posted, link not written", logs[-1])

    def test_a_raised_branch_is_named_differently(self) -> None:
        rc, logs = run_daily(image_err=RuntimeError("boom"))
        self.assertEqual(rc, 99)
        self.assertIn("post=99 (a Note branch raised)", logs[-1])

    def test_a_failed_post_keeps_its_bare_code(self) -> None:
        rc, logs = run_daily(image_rc=9)
        self.assertEqual(rc, 9)
        self.assertTrue(logs[-1].endswith("post=9"), logs[-1])


class ReportingPipelineTests(unittest.TestCase):
    def test_link_not_written_is_named_in_the_alert(self) -> None:
        self.assertEqual(record(LINK_NOT_WRITTEN), [(
            "Substack Daily Pipeline",
            f"step reported failure (exit code {LINK_NOT_WRITTEN}: "
            "Note posted, link not written: fill it in by hand)",
        )])

    def test_each_condition_reads_differently(self) -> None:
        reasons = {rc: record(rc)[0][1] for rc in (LINK_NOT_WRITTEN, 99, 9)}
        self.assertEqual(reasons[99], "step reported failure (exit code 99: a Note branch raised)")
        self.assertEqual(reasons[9], "step reported failure (exit code 9)")
        self.assertEqual(len(set(reasons.values())), 3)

    def test_success_records_nothing(self) -> None:
        self.assertEqual(record(0), [])


if __name__ == "__main__":
    unittest.main()
