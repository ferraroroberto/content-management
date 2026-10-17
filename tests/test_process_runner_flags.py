r"""Pin the ``creationflags`` the control panel's process runner passes (issue #408).

``start_pipeline`` and ``stop_pipeline``'s ``taskkill`` must take their
no-console-window flag from ``config.no_window.NO_WINDOW`` (fleet-config#399)
rather than hand-rolling ``subprocess.CREATE_NO_WINDOW``. ``NO_WINDOW`` is
patched to a sentinel bit so a hand-rolled flag would show up as a mismatch.

No real process is spawned: ``subprocess.Popen`` / ``subprocess.run`` and the
Streamlit session-state helpers are mocks.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from app import process_runner as pr

SENTINEL = 0x40000000  # not CREATE_NO_WINDOW (0x08000000), so the source is observable


@unittest.skipUnless(sys.platform == "win32", "creationflags are Windows-only")
class ProcessRunnerFlagsTests(unittest.TestCase):
    def test_start_pipeline_flags(self):
        proc = mock.Mock()
        proc.stdout = iter(())
        proc.wait.return_value = 0
        with mock.patch.object(pr, "NO_WINDOW", SENTINEL), \
                mock.patch.object(pr, "is_running", return_value=False), \
                mock.patch.object(pr, "_get_lines", return_value=mock.MagicMock()), \
                mock.patch.object(pr, "_set_proc"), \
                mock.patch.object(pr, "_set_meta"), \
                mock.patch.object(pr.subprocess, "Popen", return_value=proc) as popen:
            pr.start_pipeline("demo", ["python", "-V"])
        self.assertEqual(
            popen.call_args.kwargs["creationflags"],
            subprocess.CREATE_NEW_PROCESS_GROUP | SENTINEL,
        )

    def test_stop_pipeline_taskkill_flags(self):
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        with mock.patch.object(pr, "NO_WINDOW", SENTINEL), \
                mock.patch.object(pr, "_get_proc", return_value=proc), \
                mock.patch.object(pr.subprocess, "run") as run:
            pr.stop_pipeline("demo")
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        self.assertEqual(run.call_args.kwargs["creationflags"], SENTINEL)


if __name__ == "__main__":
    unittest.main()
