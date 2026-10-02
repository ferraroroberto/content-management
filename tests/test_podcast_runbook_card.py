"""The Podcast tab's runbook card renders docs/podcast-runbook.md from disk (issue #348)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import tab_podcast  # noqa: E402


def _card_script() -> None:
    from app import tab_podcast  # noqa: PLC0415

    tab_podcast.render_runbook()


class RunbookCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = tab_podcast.RUNBOOK

    def tearDown(self) -> None:
        tab_podcast.RUNBOOK = self._original

    def test_runbook_doc_exists_in_the_repo(self) -> None:
        self.assertEqual(tab_podcast.RUNBOOK, REPO_ROOT / "docs" / "podcast-runbook.md")
        self.assertTrue(tab_podcast.RUNBOOK.is_file())

    def test_card_is_collapsed_and_renders_the_file_read_at_render_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "runbook.md"
            doc.write_text("# Runbook\n\nfirst version\n", encoding="utf-8")
            tab_podcast.RUNBOOK = doc
            at = AppTest.from_function(_card_script).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.expander[0].label, tab_podcast.RUNBOOK_LABEL)
            self.assertFalse(at.expander[0].proto.expanded)
            self.assertEqual(at.expander[0].markdown[0].value, "# Runbook\n\nfirst version")

            doc.write_text("# Runbook\n\nedited\n", encoding="utf-8")
            at.run()
            self.assertEqual(at.expander[0].markdown[0].value, "# Runbook\n\nedited")

    def test_missing_file_warns_instead_of_failing_the_tab(self) -> None:
        tab_podcast.RUNBOOK = Path(tempfile.gettempdir()) / "no-such-runbook.md"
        at = AppTest.from_function(_card_script).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.expander[0].warning), 1)


if __name__ == "__main__":
    unittest.main()
