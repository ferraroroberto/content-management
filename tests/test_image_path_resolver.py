"""One illustration-path resolver for every planning caller (issue #371).

``planning/_wip_rows.resolve_image_path`` is the only resolver; LinkedIn and
the Substack Note used to carry their own variants. The equivalence tests
below drive each caller through its real entry point and pass on the old
per-module copies and on the shared function alike. The drift tests pin the
three cases where the copies disagreed, each resolved to the safer behaviour:

* comma-joined bare names (``"a, b"``) — LinkedIn appended ``.png`` to the
  whole string and then took ``"a"`` without an extension, so the file was
  never found; the shared resolver takes ``"a"`` and then appends -> ``a.png``;
* a bare name on the Substack Note — the Substack copy never appended
  ``.png``; it now resolves like every other platform;
* a blank first name (``"  "`` or ``", b.png"``) — the shared copy resolved
  to the illustrations folder itself and returned it as the image; it now
  raises the caller's "no filename" error, as the Substack copy did for
  ``"  "``.

No Notion, no browser: the Substack test stubs the Notion helpers and stops
``post_note`` at its backend check, right after the image is resolved.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/test_image_path_resolver.py -q
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Optional
from unittest import mock

from planning import _wip_rows as wr
from planning.instagram import schedule_instagram_posts as ig
from planning.linkedin import schedule_linkedin_posts as li
from planning.substack import post_substack_note as sn

SUBSTACK_EMPTY = "Notion editorial row has no image filename for this date."


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        for name in ("a.png", "b.png"):
            (self.folder / name).write_bytes(b"png")


def _substack_resolve(folder: Path, image_filename: Optional[str]) -> Path:
    """Run ``post_note`` up to its image resolution and return the logged path."""
    fields = {"post_url": None, "text_body": "hello", "image_filename": image_filename}
    cfg = {
        "notion_columns": {},
        "editorial_db_id": "db",
        "illustrations_folder": str(folder),
        "note_source": "stop-here",  # unknown backend -> returns 2 before any browser
    }
    with mock.patch.object(sn, "load_notion_token", return_value="t"), \
            mock.patch.object(sn, "init_notion_client", return_value=object()), \
            mock.patch.object(sn, "get_row_by_day", return_value={"id": "p"}), \
            mock.patch.object(sn, "get_field", side_effect=lambda _row, key, _cols: fields[key]), \
            mock.patch.object(sn.logger, "info") as info:
        rc = sn.post_note(cfg, "20260101", dry_run=True)
    assert rc == 2, rc
    used = [c.args[1] for c in info.call_args_list if c.args and "Using image" in c.args[0]]
    return Path(used[0])


def _li_resolve(folder: Path, title: str) -> Path:
    """LinkedIn's two-step path: the illustration loader's ``.png`` suffixing, then the resolver."""
    fname = title.strip()
    if fname and not fname.lower().endswith(".png"):
        fname = f"{fname}.png"
    return li.resolve_image_path(str(folder), fname)


def _shared_payload(folder: Path, image_filename: str) -> Path:
    """Twitter / Threads route: ``resolve_payload`` with a stubbed Notion lookup."""
    row = wr.ScheduleRow("p", date(2026, 1, 1), ["ill"], "caption", None)
    cfg = {"illustration_columns": {}, "illustrations_folder": str(folder)}
    with mock.patch.object(wr, "illustration_filename", return_value=image_filename):
        return wr.resolve_payload(object(), cfg, row, platform_label="TW").image_path


class EquivalenceTests(_Folder):
    """Same result on the pre-#371 copies and on the shared resolver."""

    def test_linkedin(self) -> None:
        self.assertEqual(_li_resolve(self.folder, "a"), self.folder / "a.png")
        self.assertEqual(_li_resolve(self.folder, "a.png, b.png"), self.folder / "a.png")
        with self.assertRaisesRegex(FileNotFoundError, "no filename"):
            li.resolve_image_path(str(self.folder), "")
        with self.assertRaisesRegex(FileNotFoundError, "Illustration not found"):
            _li_resolve(self.folder, "missing")

    def test_substack_note(self) -> None:
        self.assertEqual(_substack_resolve(self.folder, "a.png"), self.folder / "a.png")
        self.assertEqual(_substack_resolve(self.folder, "a.png, b.png"), self.folder / "a.png")
        for blank in (None, "", "   "):
            with self.subTest(blank=blank), self.assertRaisesRegex(FileNotFoundError, SUBSTACK_EMPTY):
                _substack_resolve(self.folder, blank)
        with self.assertRaisesRegex(FileNotFoundError, "Illustration not found"):
            _substack_resolve(self.folder, "missing.png")

    def test_twitter_threads_and_instagram(self) -> None:
        self.assertEqual(_shared_payload(self.folder, "a"), self.folder / "a.png")
        self.assertEqual(_shared_payload(self.folder, "a, b"), self.folder / "a.png")
        self.assertEqual(ig.resolve_image_path(str(self.folder), "b.png"), self.folder / "b.png")
        with self.assertRaisesRegex(FileNotFoundError, "no filename"):
            _shared_payload(self.folder, "")


class DriftTests(_Folder):
    """Where the copies disagreed, the safer behaviour wins (fails on the old copies)."""

    def test_callers_share_one_resolver(self) -> None:
        self.assertIs(li.resolve_image_path, wr.resolve_image_path)
        self.assertIs(sn.resolve_image_path, wr.resolve_image_path)
        self.assertIs(ig.resolve_image_path, wr.resolve_image_path)

    def test_linkedin_comma_joined_bare_names_take_the_first_png(self) -> None:
        self.assertEqual(_li_resolve(self.folder, "a, b"), self.folder / "a.png")

    def test_substack_bare_name_gets_png_suffix(self) -> None:
        self.assertEqual(_substack_resolve(self.folder, "a"), self.folder / "a.png")

    def test_blank_first_name_never_resolves_to_the_folder(self) -> None:
        for blank in ("   ", ", b.png"):
            with self.subTest(blank=blank), self.assertRaisesRegex(FileNotFoundError, "no filename"):
                _shared_payload(self.folder, blank)
        with self.assertRaisesRegex(FileNotFoundError, SUBSTACK_EMPTY):
            _substack_resolve(self.folder, ", b.png")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
