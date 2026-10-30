"""An unreadable triage state or lessons file is logged and never silently overwritten; an unparseable lessons
reply is logged apart from a valid empty one. No network, no Supabase."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import lessons, score  # noqa: E402
from newsletter.triage import state as st  # noqa: E402


class StateFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.json"

    def test_round_trip_and_absent_file(self) -> None:
        self.assertEqual(st.load_state(self.path), {})
        st.save_state({"reviewed_until": "2026-08-22"}, self.path)
        self.assertEqual(st.load_state(self.path), {"reviewed_until": "2026-08-22"})

    def test_corrupt_file_is_logged_and_not_overwritten(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertLogs("newsletter_triage.state", level="WARNING"):
            self.assertEqual(st.load_state(self.path), {})
        with self.assertLogs("newsletter_triage.state", level="ERROR"):
            with self.assertRaises(st.StateUnreadableError):
                st.save_state({"runs": []}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")


class LessonsTests(unittest.TestCase):
    def test_unreadable_lessons_file_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lessons.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertLogs("newsletter_triage.score", level="WARNING"):
                self.assertEqual(score.load_lessons(path), [])
            path.write_text(json.dumps({"lessons": ["keep it short"]}), encoding="utf-8")
            self.assertEqual(score.load_lessons(path), ["keep it short"])

    def _propose(self, reply: str):
        run = {"window_start": "2026-08-14", "window_end": "2026-08-22"}
        deltas = {"dropped": [], "promoted": [], "noted": []}
        with mock.patch.object(lessons.db, "get_run", return_value=run), \
                mock.patch.object(lessons.db, "get_review", return_value={"comment": ""}), \
                mock.patch.object(lessons.db, "candidates", return_value=[]), \
                mock.patch.object(lessons.db, "load_decisions", return_value=[]), \
                mock.patch.object(lessons, "review_deltas", return_value=deltas), \
                mock.patch.object(lessons, "build_prompt", return_value="p"), \
                mock.patch.object(lessons.llm, "call", return_value=reply):
            return lessons.propose(1, base_url="http://x", model="m", criteria={})

    def test_unparseable_reply_warns_but_empty_reply_does_not(self) -> None:
        with self.assertLogs("newsletter_triage.lessons", level="WARNING"):
            self.assertEqual(self._propose("not json at all"), [])
        with self.assertNoLogs("newsletter_triage.lessons", level="WARNING"):
            self.assertEqual(self._propose('{"lessons": []}'), [])


if __name__ == "__main__":
    unittest.main()
