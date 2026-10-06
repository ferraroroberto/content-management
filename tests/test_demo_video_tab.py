"""The control panel's demo video tab (issue #362): runbook card, stage buttons, command lines."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import tab_demo_video  # noqa: E402

EXAMPLE = REPO_ROOT / "demo_video" / "examples" / "facilitation-suite"


def _card_script() -> None:
    from app import tab_demo_video  # noqa: PLC0415

    tab_demo_video.render_runbook()


def _tab_script() -> None:
    from app import tab_demo_video  # noqa: PLC0415

    tab_demo_video.run()


class RunbookCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = tab_demo_video.RUNBOOK

    def tearDown(self) -> None:
        tab_demo_video.RUNBOOK = self._original

    def test_runbook_doc_exists_in_the_repo(self) -> None:
        self.assertEqual(tab_demo_video.RUNBOOK, REPO_ROOT / "docs" / "demo-video-runbook.md")
        self.assertTrue(tab_demo_video.RUNBOOK.is_file())

    def test_card_is_collapsed_and_renders_the_file_read_at_render_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "runbook.md"
            doc.write_text("# Runbook\n\nfirst version\n", encoding="utf-8")
            tab_demo_video.RUNBOOK = doc
            at = AppTest.from_function(_card_script).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.expander[0].label, tab_demo_video.RUNBOOK_LABEL)
            self.assertFalse(at.expander[0].proto.expanded)
            self.assertEqual(at.expander[0].markdown[0].value, "# Runbook\n\nfirst version")

    def test_missing_file_warns_instead_of_failing_the_tab(self) -> None:
        tab_demo_video.RUNBOOK = Path(tempfile.gettempdir()) / "no-such-runbook.md"
        at = AppTest.from_function(_card_script).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.expander[0].warning), 1)


class CommandTests(unittest.TestCase):
    def test_each_button_builds_its_pipeline_command(self) -> None:
        folder = Path("C:/demos/x")
        base = [str(tab_demo_video.VENV_PY), "demo_video_pipeline.py", str(folder)]
        build = tab_demo_video.build_command
        self.assertEqual(build(folder, "🧰 prep"), [*base, "--stages", "prep"])
        self.assertEqual(build(folder, "🛡️ check", cut="en-linkedin"), [*base, "--stages", "check", "--cut", "en-linkedin"])
        self.assertEqual(build(folder, "👀 preview", cut="en-square", force=True),
                         [*base, "--preview", "--cut", "en-square", "--force"])
        self.assertEqual(build(folder, "🎬 render", cut=tab_demo_video.ALL_CUTS), base)
        self.assertEqual(build(folder, "ℹ️ status", cut="en-linkedin", force=True), [*base, "--status"])
        self.assertEqual(build(folder, "🛡️ check", force=True), [*base, "--stages", "check"])  # nothing to redo

    def test_record_is_not_a_button(self) -> None:
        self.assertFalse(any("record" in " ".join(args) for args in tab_demo_video.ACTIONS.values()))


class TabTests(unittest.TestCase):
    def test_a_demo_folder_shows_its_cuts_and_the_stage_buttons(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            for f in ("demo.json", "marks.json", "roster.txt"):
                shutil.copy(EXAMPLE / f, tmp / f)
            at = AppTest.from_function(_tab_script)
            at.session_state["demo-video-folder"] = str(tmp)
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.dataframe), 1)
            labels = [b.label for b in at.button]
            self.assertEqual(labels[:len(tab_demo_video.ACTIONS)], list(tab_demo_video.ACTIONS))  # then the log panel's own
            self.assertIn("en-portrait", at.selectbox[0].options)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_folder_without_demo_json_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            at = AppTest.from_function(_tab_script)
            at.session_state["demo-video-folder"] = tmp
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any("demo.json" in w.value for w in at.warning))
            self.assertEqual(len(at.button), 0)


if __name__ == "__main__":
    unittest.main()
