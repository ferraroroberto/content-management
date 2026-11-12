"""A planning run killed mid-stage names the stages it never ran and alerts the owner.

The planning pipeline writes an in-flight ``running`` record before each stage; a
killed process leaves it behind. The synthetic fixture here is such a record (no
post text, no account names): the run died in Instagram, so Twitter, Threads and
Videos never started. The daily reporting run reads the record and raises its
consolidated alert; nothing here runs a stage, a scheduler or a network call.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import reporting_pipeline as rp
from config.no_window import NO_WINDOW
from planning import run_state

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Same shape as test_planning_run_state: the real orchestrator, stages stubbed, and
# Instagram ends the process with no `finally`, no `atexit` and no final write.
_KILL_IN_INSTAGRAM = """
import logging, os, sys
from pathlib import Path
import planning_pipeline as pp
from planning import run_state

run_state.RESULTS_DIR = Path(sys.argv[1])
pp.configure_logger = lambda debug: setattr(pp, "logger", logging.getLogger("kill_probe"))
pp.run_clone = lambda: 0
pp.run_linkedin = lambda: (0, [])
pp.run_instagram = lambda: os._exit(1)
sys.argv = ["planning_pipeline.py"] + sys.argv[2:]
pp.main()
"""


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=NO_WINDOW)
    child.wait(timeout=60)
    return child.pid


def _killed_record(**overrides) -> dict:
    base = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "mode": "LIVE",
        "verdict": "running",
        "pid": _dead_pid(),
        "stage": "Instagram",
        "completed_stages": ["Clone", "LinkedIn"],
        "skipped_stages": [],
    }
    base.update(overrides)
    return base


class NotRunStagesTests(unittest.TestCase):
    def test_stages_after_the_one_that_died_are_not_run(self):
        self.assertEqual(run_state.not_run_stages(_killed_record()), ["Twitter", "Threads", "Videos"])

    def test_a_stage_the_operator_skipped_is_not_reported_as_not_run(self):
        record = _killed_record(skipped_stages=["Videos"])
        self.assertEqual(run_state.not_run_stages(record), ["Twitter", "Threads"])

    def test_a_record_from_before_skipped_stages_were_written_still_reads(self):
        record = _killed_record()
        del record["skipped_stages"]
        self.assertEqual(run_state.not_run_stages(record), ["Twitter", "Threads", "Videos"])

    def test_resume_command_skips_what_finished_and_keeps_the_mode(self):
        command = run_state.resume_command(_killed_record())
        self.assertEqual(
            command,
            "python planning_pipeline.py --live --skip-clone --skip-linkedin",
        )
        dry = run_state.resume_command(_killed_record(mode="DRY-RUN", skipped_stages=["Videos"]))
        self.assertEqual(
            dry,
            "python planning_pipeline.py --dry-run --skip-clone --skip-linkedin --skip-videos",
        )


class KilledRunRecordTests(unittest.TestCase):
    def _run_and_load(self, *extra: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-c", _KILL_IN_INSTAGRAM, tmp, *extra],
                cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
                creationflags=NO_WINDOW,
            )
            self.assertEqual(proc.returncode, 1, proc.stderr)
            return json.loads((Path(tmp) / run_state.LATEST_NAME).read_text(encoding="utf-8"))

    def test_a_run_killed_mid_stage_reports_the_later_platforms_as_not_run(self):
        latest = self._run_and_load("--dry-run")
        self.assertEqual(run_state.effective_verdict(latest), "interrupted")
        self.assertEqual(latest["stage"], "Instagram")
        self.assertEqual(run_state.not_run_stages(latest), ["Twitter", "Threads", "Videos"])

    def test_the_record_remembers_which_stages_the_operator_skipped(self):
        latest = self._run_and_load("--dry-run", "--skip-videos")
        self.assertEqual(latest["skipped_stages"], ["Videos"])
        self.assertEqual(run_state.not_run_stages(latest), ["Twitter", "Threads"])


class ReportingAlertTests(unittest.TestCase):
    def setUp(self):
        rp.logger = MagicMock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.results_dir = Path(tmp.name)
        patch = mock.patch.object(run_state, "RESULTS_DIR", self.results_dir)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, record: dict) -> None:
        (self.results_dir / run_state.LATEST_NAME).write_text(json.dumps(record), encoding="utf-8")

    def _check(self) -> "rp.PipelineFailures":
        failures = rp.PipelineFailures()
        rp.check_planning_run(failures)
        return failures

    def test_a_killed_run_trips_the_failure_alert_naming_the_stages_never_run(self):
        self._write(_killed_record())
        failures = self._check()

        self.assertTrue(failures.any())
        message = rp._build_alert_message(failures, "2026-11-10")
        self.assertIn("Planning run interrupted", message)
        self.assertIn("Instagram", message)
        self.assertIn("Not run: Twitter, Threads, Videos", message)
        self.assertIn("python planning_pipeline.py --live --skip-clone --skip-linkedin", message)

    def test_the_alert_is_sent_without_the_planning_tab(self):
        self._write(_killed_record())
        failures = self._check()
        notify = MagicMock()
        notify.main.return_value = 0
        with mock.patch.object(rp, "_load_notify_send", return_value=notify):
            rp.send_failure_alert(failures, "2026-11-10")

        args = notify.main.call_args.args[0]
        self.assertEqual(args[:2], ["--category", "attention"])
        self.assertIn("Not run: Twitter, Threads, Videos", args[-1])

    def test_a_clean_finished_run_raises_nothing(self):
        self._write({"verdict": "clean", "started_at": "2026-11-07T21:02:00"})
        self.assertFalse(self._check().any())

    def test_a_run_still_alive_raises_nothing(self):
        self._write(_killed_record(pid=os.getpid()))
        self.assertFalse(self._check().any())

    def test_no_record_yet_raises_nothing(self):
        self.assertFalse(self._check().any())

    def test_an_unreadable_record_is_unverified_never_a_pass(self):
        (self.results_dir / run_state.LATEST_NAME).write_text("{not json", encoding="utf-8")
        failures = self._check()

        self.assertTrue(failures.any())
        self.assertTrue(any(e.startswith("planning_run:") for e in failures.unverified), failures.unverified)


if __name__ == "__main__":
    unittest.main()
