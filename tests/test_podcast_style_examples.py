"""Style examples come only from the newest N episodes, newest first (issue #354) — fake Notion rows."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcast import clips  # noqa: E402
from podcast.episode import Episode  # noqa: E402

MARK = "A clip from my conversation"


def _text(kind: str, value: str) -> dict:
    return {"type": kind, kind: [{"plain_text": value}]}


def _clip(cid: str, title: str, number: int, intro: str = "") -> dict:
    li = f"{intro}\n\n{MARK} with the inspiring someone" if intro else ""
    return {"id": cid, "properties": {"clip": _text("title", title), "TextLI": _text("rich_text", li),
                                      "number": {"type": "number", "number": number}}}


def _episode(date: str, clip_ids: list[str]) -> dict:
    return {"id": f"ep-{date}", "properties": {
        "Date": {"type": "date", "date": {"start": date} if date else None},
        "clips": {"type": "relation", "relation": [{"id": c} for c in clip_ids]}}}


EPISODES = [
    _episode("2024-01-10", ["old1", "old2"]),
    _episode("2025-11-25", ["new2", "new1"]),
    _episode("2025-06-02", []),                     # dated, but no clips yet: skipped
    _episode("2025-07-30", ["mid1"]),
    _episode("2025-06-12", ["third1"]),
    _episode("", ["undated1"]),
]
CLIPS = [
    _clip("old1", "an old style title", 1, "old intro"), _clip("old2", "another old title", 2),
    _clip("new1", "newest first clip", 1, "newest intro"), _clip("new2", "newest second clip", 2),
    _clip("mid1", "middle episode clip", 1, "middle intro"), _clip("third1", "third episode clip", 1),
    _clip("undated1", "undated clip", 1),
]


class PickStyleExamplesTests(unittest.TestCase):
    def test_only_the_newest_n_episodes_newest_first(self) -> None:
        picked = clips.pick_style_examples(EPISODES, CLIPS, 3)
        self.assertEqual(picked["titles"], ["newest first clip", "newest second clip", "middle episode clip",
                                            "third episode clip"])
        self.assertEqual(picked["linkedin"], ["newest intro", "middle intro"])
        self.assertEqual(picked["episodes"], 3)

    def test_n_cuts_the_episodes(self) -> None:
        picked = clips.pick_style_examples(EPISODES, CLIPS, 1)
        self.assertEqual(picked["titles"], ["newest first clip", "newest second clip"])
        self.assertEqual(picked["episodes"], 1)

    def test_caps_still_apply(self) -> None:
        many = [_clip(f"c{i}", f"title {i}", i, f"intro {i}") for i in range(60)]
        picked = clips.pick_style_examples([_episode("2025-01-01", [c["id"] for c in many])], many, 3)
        self.assertEqual(len(picked["titles"]), clips.MAX_STYLE_TITLES)
        self.assertEqual(len(picked["linkedin"]), clips.MAX_STYLE_INTROS)
        self.assertEqual(picked["titles"][:2], ["title 0", "title 1"])


class StyleExamplesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ep = Episode(folder=self.tmp / "ep", guest="Guest Person", guest_display="Guest Person",
                          guest_first="Guest", guest_pronoun_possessive="their", tracks={})
        self.cfg = {"work_dir": str(self.tmp / "work"), "style_episodes": 1,
                    "notion": {"clips_db_id": "clips-db", "episodes_db_id": "episodes-db"}}

    def _run(self) -> dict:
        rows = {"clips-db": CLIPS, "episodes-db": EPISODES}
        with mock.patch("config.loader.load_full_config", return_value={"notion": {"api_token": "x"}}), \
                mock.patch("reporting.notion._client.init_notion_client", return_value=object()), \
                mock.patch("reporting.notion.editorial.query_rows_by_filter",
                           side_effect=lambda _n, db, _f: rows[db]):
            return clips.style_examples(self.cfg, self.ep)

    def test_n_comes_from_config_and_an_old_all_episodes_cache_is_not_reused(self) -> None:
        from podcast.episode import work_dir  # noqa: PLC0415
        stale = work_dir(self.cfg, self.ep) / "style_examples.json"
        stale.write_text(json.dumps({"titles": ["an old style title"], "linkedin": ["old intro"]}),
                         encoding="utf-8")
        picked = self._run()
        self.assertEqual(picked["titles"], ["newest first clip", "newest second clip"])
        self.assertEqual(picked["linkedin"], ["newest intro"])


if __name__ == "__main__":
    unittest.main()
