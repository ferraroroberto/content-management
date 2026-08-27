"""Tri-state OneDrive placeholder detection (issue #244).

`_win_file_attributes`/`is_online_only` used to collapse "the Win32
``GetFileAttributesW`` query failed" into "definitely not a placeholder" (a
failed query returned ``-1``, and ``attrs >= 0`` read that as falsy) — so a
throttled or denied query silently reported every clip as safe to upload,
even though it never actually confirmed that. ``ensure_local_file`` then
no-op'd, handing a possible OneDrive placeholder straight to the uploader —
the issue-#104 failure mode, where a post is scheduled with no media and the
run reports success.

These tests pin the fix: a failed query is its own logged ``None`` state,
never folded into "confirmed local", and ``ensure_local_file`` treats that
``None`` as "might be a placeholder" — forcing the same download-and-verify
path it uses for a confirmed placeholder — rather than silently returning.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning.videos import videos_session as vs  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "GetFileAttributesW is Windows-only")
class WinFileAttributesTests(unittest.TestCase):
    def test_successful_query_returns_the_int_bitmask(self):
        with mock.patch.object(vs.ctypes.windll.kernel32, "GetFileAttributesW", return_value=0x20):
            self.assertEqual(vs._win_file_attributes(Path("C:/some/file.mp4")), 0x20)

    def test_failed_query_returns_none_and_logs_a_warning(self):
        with mock.patch.object(
            vs.ctypes.windll.kernel32, "GetFileAttributesW",
            return_value=vs._INVALID_FILE_ATTRIBUTES,
        ), mock.patch.object(
            vs.ctypes.windll.kernel32, "GetLastError", return_value=5,
        ), self.assertLogs(vs.logger, level="WARNING") as cm:
            result = vs._win_file_attributes(Path("C:/some/file.mp4"))
        self.assertIsNone(result)
        self.assertTrue(any("GetFileAttributesW failed" in line for line in cm.output))


class IsOnlineOnlyTests(unittest.TestCase):
    def test_indeterminate_attrs_propagate_as_none_not_false(self):
        with mock.patch.object(vs, "_win_file_attributes", return_value=None):
            self.assertIsNone(vs.is_online_only(Path("x")))

    def test_placeholder_bits_report_true(self):
        with mock.patch.object(vs, "_win_file_attributes", return_value=vs._FILE_ATTRIBUTE_OFFLINE):
            self.assertIs(vs.is_online_only(Path("x")), True)

    def test_confirmed_local_reports_false(self):
        with mock.patch.object(vs, "_win_file_attributes", return_value=0x20):  # FILE_ATTRIBUTE_ARCHIVE
            self.assertIs(vs.is_online_only(Path("x")), False)


class EnsureLocalFileIndeterminateTests(unittest.TestCase):
    """The gap this issue closes: indeterminate must not collapse into no-op."""

    def test_confirmed_local_is_a_true_noop(self):
        with mock.patch.object(vs, "is_online_only", return_value=False), \
             mock.patch.object(vs, "_trigger_download") as m_dl:
            vs.ensure_local_file(Path("x"))
        m_dl.assert_not_called()

    def test_indeterminate_forces_a_defensive_download_never_silently_returns(self):
        with mock.patch.object(vs, "is_online_only", return_value=None), \
             mock.patch.object(vs, "_trigger_download") as m_dl, \
             mock.patch.object(vs.time, "sleep"), \
             self.assertLogs(vs.logger, level="WARNING") as cm, \
             self.assertRaises(RuntimeError) as ctx:
            vs.ensure_local_file(Path("x"), timeout_s=0.01, poll_s=0.01)
        m_dl.assert_called_once()
        self.assertIn("could not be confirmed", str(ctx.exception))
        self.assertTrue(any("could not be determined" in line for line in cm.output))

    def test_indeterminate_that_resolves_local_returns_cleanly(self):
        results = iter([None, False])
        with mock.patch.object(vs, "is_online_only", side_effect=lambda p: next(results)), \
             mock.patch.object(vs, "_trigger_download") as m_dl:
            vs.ensure_local_file(Path("x"), timeout_s=5, poll_s=0.01)
        m_dl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
