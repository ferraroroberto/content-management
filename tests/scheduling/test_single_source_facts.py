"""Facts the pipelines used to spell out by hand in several places have one source.

* Every pacing wait in a converted planning driver names a tier from
  ``planning/_session_base.py``; a bare ``wait_for_timeout(<ms>)`` literal fails.
* Every ``PipelineFailures`` kind carries its alert heading, so ``any()`` and the
  alert body cannot disagree about which kinds exist.
* The planning orchestrator runs the stages ``run_state.STAGES`` lists, in order.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

import planning_pipeline
import reporting_pipeline as rp
from planning import run_state

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Drivers whose waits are all named; add a driver here when its literals are converted.
_PACED_DRIVERS = (
    "planning/instagram/instagram_composer.py",
    "planning/instagram/schedule_instagram_posts.py",
    "planning/linkedin/linkedin_composer.py",
    "planning/linkedin/schedule_linkedin_posts.py",
    "planning/threads/threads_composer.py",
    "planning/twitter/twitter_composer.py",
)


def _bare_waits(path: Path) -> list[int]:
    """Line numbers of ``wait_for_timeout`` calls whose argument is a numeric literal."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for_timeout"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]


class PacingTierTests(unittest.TestCase):
    def test_converted_drivers_have_no_bare_wait_literals(self) -> None:
        for rel in _PACED_DRIVERS:
            with self.subTest(driver=rel):
                self.assertEqual([], _bare_waits(REPO_ROOT / rel))


class PipelineFailureKindTests(unittest.TestCase):
    def test_every_kind_has_a_heading_and_trips_any_and_the_alert(self) -> None:
        for kind in fields(rp.PipelineFailures):
            with self.subTest(kind=kind.name):
                heading = kind.metadata.get("heading")
                self.assertTrue(heading)
                failures = rp.PipelineFailures()
                self.assertFalse(failures.any())
                entry = ("step", "boom") if kind.name == "step_failures" else "entry"
                getattr(failures, kind.name).append(entry)
                self.assertTrue(failures.any())
                message = rp._build_alert_message(failures, "2026-11-10")
                self.assertIn(heading, message)
                self.assertIn("• step: boom" if kind.name == "step_failures" else "• entry", message)


class StageOrderTests(unittest.TestCase):
    def test_main_runs_the_stages_run_state_lists(self) -> None:
        ran: list[str] = []

        def stub(name: str):
            def run() -> tuple[int, list[dict]]:
                ran.append(name)
                return 0, []
            return run

        def clone(_args) -> planning_pipeline.PlatformResult:
            ran.append("Clone")
            return planning_pipeline.PlatformResult(name="Clone")

        with (
            mock.patch("sys.argv", ["planning_pipeline.py", "--dry-run"]),
            mock.patch.object(planning_pipeline, "_run_clone", clone),
            mock.patch.object(planning_pipeline, "run_linkedin", stub("LinkedIn")),
            mock.patch.object(planning_pipeline, "run_instagram", stub("Instagram")),
            mock.patch.object(planning_pipeline, "run_twitter", stub("Twitter")),
            mock.patch.object(planning_pipeline, "run_threads", stub("Threads")),
            mock.patch.object(planning_pipeline, "run_videos", stub("Videos")),
            mock.patch.object(run_state, "write_summary", return_value=Path("summary.md")),
            mock.patch.object(run_state, "write_result", return_value=Path("result.json")),
        ):
            self.assertEqual(0, planning_pipeline.main())
        self.assertEqual(list(run_state.STAGES), ran)


if __name__ == "__main__":
    unittest.main()
