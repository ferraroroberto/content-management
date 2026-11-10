"""The planning pipeline's result record tells a killed run from a finished one.

A run that dies between stages cannot write a final record, so the in-flight
``running`` record it left behind must read as ``interrupted`` rather than
letting the previous clean run stand in for it.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import planning_pipeline
from config.no_window import NO_WINDOW
from planning import run_state

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Runs the real orchestrator with every stage stubbed; Instagram ends the whole
# process the way a killed one ends: no `finally`, no `atexit`, no final write.
_KILL_BETWEEN_STAGES = """
import logging, os, sys
from pathlib import Path
import planning_pipeline as pp
from planning import run_state

run_state.RESULTS_DIR = Path(sys.argv[1])
pp.configure_logger = lambda debug: setattr(pp, "logger", logging.getLogger("kill_probe"))
pp.run_clone = lambda: 0
pp.run_linkedin = lambda: (0, [])
pp.run_instagram = lambda: os._exit(1)
sys.argv = ["planning_pipeline.py", "--dry-run"]
pp.main()
"""


def _record(**overrides) -> dict:
    base = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": "running",
        "pid": os.getpid(),
        "stage": "Instagram",
        "completed_stages": ["Clone", "LinkedIn"],
    }
    base.update(overrides)
    return base


def _dead_pid() -> int:
    # The pid of a process that has already exited is what a killed run leaves behind.
    child = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=NO_WINDOW)
    child.wait(timeout=60)
    return child.pid


class EffectiveVerdictTests(unittest.TestCase):
    def test_finished_verdicts_pass_through(self):
        for verdict in ("clean", "failures"):
            self.assertEqual(run_state.effective_verdict({"verdict": verdict}), verdict)

    def test_running_with_a_live_process_stays_running(self):
        self.assertEqual(run_state.effective_verdict(_record()), "running")

    def test_running_whose_process_is_gone_is_interrupted(self):
        self.assertEqual(run_state.effective_verdict(_record(pid=_dead_pid())), "interrupted")

    def test_running_older_than_the_age_cap_is_interrupted_even_if_the_pid_lives(self):
        old = datetime.now() - timedelta(seconds=run_state.MAX_RUN_AGE_S + 60)
        record = _record(started_at=old.isoformat(timespec="seconds"))
        self.assertEqual(run_state.effective_verdict(record), "interrupted")

    def test_running_without_a_usable_pid_or_start_is_interrupted(self):
        self.assertEqual(run_state.effective_verdict(_record(pid=None)), "interrupted")
        self.assertEqual(run_state.effective_verdict(_record(started_at="not a date")), "interrupted")
        record = _record()
        del record["started_at"]
        self.assertEqual(run_state.effective_verdict(record), "interrupted")

    def test_pid_alive_tells_this_process_from_a_finished_one(self):
        self.assertTrue(run_state.pid_alive(os.getpid()))
        self.assertFalse(run_state.pid_alive(_dead_pid()))


class PipelineRecordTests(unittest.TestCase):
    def test_killed_between_stages_leaves_an_interrupted_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-c", _KILL_BETWEEN_STAGES, tmp],
                cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
                creationflags=NO_WINDOW,
            )
            self.assertEqual(proc.returncode, 1, proc.stderr)
            latest = json.loads((Path(tmp) / run_state.LATEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(latest["verdict"], "running")
            self.assertEqual(latest["stage"], "Instagram")
            self.assertEqual(latest["completed_stages"], ["Clone", "LinkedIn"])
            self.assertEqual(run_state.effective_verdict(latest), "interrupted")
            self.assertEqual(list(Path(tmp).glob("*-summary.md")), [], "a killed run writes no summary")

    def test_finished_run_replaces_the_in_flight_record(self):
        seen: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            def stage() -> tuple[int, list]:
                record = json.loads((Path(tmp) / run_state.LATEST_NAME).read_text(encoding="utf-8"))
                seen.append((record["verdict"], record["stage"]))
                return 0, []

            def configure(debug: bool) -> logging.Logger:
                planning_pipeline.logger = logging.getLogger("run_state_test")
                return planning_pipeline.logger

            patches = [
                mock.patch.object(run_state, "RESULTS_DIR", Path(tmp)),
                mock.patch.object(planning_pipeline, "configure_logger", configure),
                mock.patch.object(planning_pipeline, "run_clone", lambda: 0),
                mock.patch.object(planning_pipeline, "run_linkedin", stage),
                mock.patch.object(planning_pipeline, "run_instagram", stage),
                mock.patch.object(planning_pipeline, "run_twitter", stage),
                mock.patch.object(planning_pipeline, "run_threads", stage),
                mock.patch.object(planning_pipeline, "run_videos", stage),
                mock.patch.object(sys, "argv", ["planning_pipeline.py", "--dry-run"]),
            ]
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(planning_pipeline.main(), 0)

            self.assertEqual(seen, [("running", n) for n in
                                    ("LinkedIn", "Instagram", "Twitter", "Threads", "Videos")])
            latest = json.loads((Path(tmp) / run_state.LATEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(latest["verdict"], "clean")
            self.assertEqual(run_state.effective_verdict(latest), "clean")
            stamped = [p for p in Path(tmp).glob("*-result.json") if p.name != run_state.LATEST_NAME]
            self.assertEqual(len(stamped), 1)
            self.assertEqual(json.loads(stamped[0].read_text(encoding="utf-8")), latest)


if __name__ == "__main__":
    unittest.main()
