"""One Substack Note publish sequence for the image and video Notes (issue #371).

``post_substack_note.publish_note_flow`` runs the backend check, the native
dry run / publish, or open composer → fill → dry-run screenshot → Post-ready
wait → click Post → resolve URL → link write, for both publishers. The video
publisher used to carry its own ~100-line copy.

Each test drives a publisher's real entry point (``post_note`` and
``_post_video_note_for_row``). Notion, the native API, the composer fill and
the session are fakes, and the composer helpers are patched in whichever
module defines them. The same tests run on the pre-#371 copies and on the
shared flow:

* ``EquivalenceTests`` pass on both: every exit code, the failure-screenshot
  labels, the dry-run screenshot names, each publisher's own Post click and
  dialog-close budget, the video-only Post-ready wait, and the link column
  each one writes.
* ``DriftTests`` pin the one place the copies disagreed. The video copy
  swallowed a failed link write and returned 0, so the row looked unposted,
  and the next run posted the Note a second time with nobody alerted. The
  image copy let the error raise, which the daily pipeline reports as rc 99.
  The shared flow raises for both (fails on the old video copy).

No browser, no network, no Notion, no Substack.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/test_substack_note_publish.py -q
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

from playwright.sync_api import TimeoutError as PWTimeoutError

from planning.substack import post_substack_note as sn
from planning.substack import post_substack_video_note as vn
from planning.substack.substack_session import LoginRequiredError
from planning.videos.videos_session import VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS, ClipPayload

DAY = "20260929"
URL = "https://example.invalid/@someone/note/c-1"
VIDEO_COLS = {"post_url_sb": "link SB(v)"}


class _Run:
    """One publisher run: its exit code, the fakes it touched, or what it raised."""

    def __init__(self) -> None:
        self.session = mock.MagicMock()
        self.notion = object()
        self.rc: Optional[int] = None
        self.m: dict[str, mock.Mock] = {}

    @property
    def dialog_wait(self) -> mock.Mock:
        return self.session.page.get_by_role.return_value.first.wait_for

    def failure_shots(self) -> list[str]:
        return [c.args[0] for c in self.session.screenshot_failure.call_args_list]


def _patch_composer(stack: contextlib.ExitStack, run: _Run, *, open_err, url) -> None:
    """Patch the composer helpers in every module that binds them."""
    for name, kwargs in (("_open_note_composer", {"side_effect": open_err}),
                         ("_resolve_published_note_url", {"return_value": url})):
        mocked = mock.Mock(**kwargs)
        for module in (sn, vn):
            if hasattr(module, name):
                stack.enter_context(mock.patch.object(module, name, mocked))
        run.m[name] = mocked


def _configure(run: _Run, *, login_err, close_err, ready_err) -> None:
    if login_err:
        run.session.goto_with_login_check.side_effect = login_err
    if close_err:
        run.dialog_wait.side_effect = close_err
    if ready_err:
        run.session.page.wait_for_function.side_effect = ready_err


def run_image(source: str = "playwright", *, dry_run: bool = False, native_err=None,
              login_err=None, open_err=None, fill_err=None, close_err=None,
              url: Optional[str] = URL, write_err=None) -> _Run:
    run = _Run()
    _configure(run, login_err=login_err, close_err=close_err, ready_err=None)
    fields = {"post_url": None, "text_body": "hello", "image_filename": "a.png"}
    with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
        (Path(folder) / "a.png").write_bytes(b"png")
        cfg = {"notion_columns": {}, "editorial_db_id": "db", "illustrations_folder": folder,
               "note_source": source, "profile_url": "https://example.invalid/profile"}
        patches = {
            "load_notion_token": {"return_value": "t"},
            "init_notion_client": {"return_value": run.notion},
            "get_row_by_day": {"return_value": {"id": "row"}},
            "get_field": {"side_effect": lambda _row, key, _cols: fields[key]},
            "get_property_type": {"return_value": "url"},
            "set_field": {"side_effect": write_err},
            "_fill_note_dialog": {"side_effect": fill_err},
            "_publish_native": {"return_value": URL, "side_effect": native_err},
            "SubstackSession": {},
        }
        for name, kwargs in patches.items():
            run.m[name] = stack.enter_context(mock.patch.object(sn, name, **kwargs))
        _patch_composer(stack, run, open_err=open_err, url=url)
        run.rc = sn.post_note(cfg, DAY, dry_run=dry_run, session=run.session)
    return run


def run_video(source: str = "playwright", *, dry_run: bool = False, native_err=None,
              login_err=None, open_err=None, fill_err=None, close_err=None,
              ready_err=None, url: Optional[str] = URL, write_err=None) -> _Run:
    run = _Run()
    _configure(run, login_err=login_err, close_err=close_err, ready_err=ready_err)
    payload = ClipPayload(
        clip_page_id="clip", title="clip", video_path=Path("clip.mp4"),
        thumb_path=Path("clip.png"), caption_short="short", caption_long="long",
    )
    cfg = {"note_source": source, "profile_url": "https://example.invalid/profile"}
    with contextlib.ExitStack() as stack:
        patches = {
            "init_notion_client": {"return_value": run.notion},
            "load_clip_payload": {"return_value": payload},
            "set_field": {"side_effect": write_err},
            "_fill_video_note_dialog": {"side_effect": fill_err},
            "_publish_native": {"return_value": URL, "side_effect": native_err},
        }
        for name, kwargs in patches.items():
            run.m[name] = stack.enter_context(mock.patch.object(vn, name, **kwargs))
        _patch_composer(stack, run, open_err=open_err, url=url)
        run.rc = vn._post_video_note_for_row(
            cfg, {"editorial_columns": VIDEO_COLS, "clip_columns": {}}, "t",
            {"id": "row", "properties": {}}, dry_run=dry_run, force=False, session=run.session,
        )
    return run


PUBLISHERS = (("image", run_image, DAY), ("video", run_video, "clip"))


class EquivalenceTests(unittest.TestCase):
    """Same behaviour on the pre-#371 copies and on ``publish_note_flow``."""

    def each(self):
        for name, run, label in PUBLISHERS:
            with self.subTest(publisher=name):
                yield name, run, label

    def test_unknown_backend(self) -> None:
        for _, run, _ in self.each():
            self.assertEqual(run("carrier-pigeon").rc, 2)

    def test_native_dry_run_publishes_nothing(self) -> None:
        for _, run, _ in self.each():
            r = run("native", dry_run=True)
            self.assertEqual(r.rc, 0)
            r.m["_publish_native"].assert_not_called()
            r.m["set_field"].assert_not_called()

    def test_native_publish_writes_the_link_without_a_browser(self) -> None:
        for name, run, _ in self.each():
            r = run("native")
            self.assertEqual(r.rc, 0)
            r.session.goto_with_login_check.assert_not_called()
            if name == "image":
                r.m["set_field"].assert_called_once_with(r.notion, "row", "post_url", URL, {}, "url")
            else:
                r.m["set_field"].assert_called_once_with(r.notion, "row", "post_url_sb", URL, VIDEO_COLS, "url")

    def test_native_publish_failure(self) -> None:
        for _, run, _ in self.each():
            r = run("native", native_err=RuntimeError("cookie expired"))
            self.assertEqual(r.rc, 9)
            r.m["set_field"].assert_not_called()

    def test_image_native_backend_never_opens_a_session(self) -> None:
        r = run_image("native")
        r.m["SubstackSession"].assert_not_called()

    def test_login_required(self) -> None:
        for _, run, _ in self.each():
            self.assertEqual(run(login_err=LoginRequiredError("signed out")).rc, 6)

    def test_composer_open_and_fill_failures(self) -> None:
        for _, run, label in self.each():
            r = run(open_err=RuntimeError("no Create button"))
            self.assertEqual((r.rc, r.failure_shots()), (7, [f"{label}-selector-fail"]))
            r = run(fill_err=RuntimeError("no editor"))
            self.assertEqual((r.rc, r.failure_shots()), (8, [f"{label}-fill-fail"]))

    def test_playwright_dry_run_screenshots_and_stops(self) -> None:
        for name, run, _ in self.each():
            r = run(dry_run=True)
            self.assertEqual(r.rc, 0)
            shot = Path(r.session.page.screenshot.call_args.kwargs["path"])
            self.assertEqual(shot.parent.name, "substack")
            suffix = f"{DAY}-dryrun.png" if name == "image" else "-video-dryrun.png"
            self.assertTrue(shot.name.endswith(suffix), shot.name)
            r.dialog_wait.assert_not_called()
            r.m["set_field"].assert_not_called()

    def test_each_publisher_keeps_its_post_click_and_close_budget(self) -> None:
        image = run_image()
        image.session.page.get_by_role.return_value.first.get_by_role.assert_called_once()
        self.assertEqual(image.dialog_wait.call_args.kwargs, {"state": "hidden", "timeout": 30000})
        image.session.page.wait_for_function.assert_not_called()

        video = run_video()
        self.assertIn('data-testid="composer-post"', video.session.page.locator.call_args.args[0])
        self.assertEqual(video.dialog_wait.call_args.kwargs, {"state": "hidden", "timeout": 120000})
        self.assertEqual(video.session.page.wait_for_function.call_args.kwargs,
                         {"timeout": VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS})

    def test_video_post_never_enabled(self) -> None:
        r = run_video(ready_err=PWTimeoutError("upload still running"))
        self.assertEqual((r.rc, r.failure_shots()), (9, ["clip-post-disabled"]))
        r.dialog_wait.assert_not_called()

    def test_post_click_or_close_failure(self) -> None:
        for _, run, label in self.each():
            r = run(close_err=RuntimeError("dialog still open"))
            self.assertEqual((r.rc, r.failure_shots()), (9, [f"{label}-post-fail"]))
            r.m["set_field"].assert_not_called()

    def test_unresolved_url(self) -> None:
        for _, run, _ in self.each():
            r = run(url=None)
            self.assertEqual(r.rc, 10)
            r.m["set_field"].assert_not_called()

    def test_published_note_writes_its_link_column(self) -> None:
        for name, run, _ in self.each():
            r = run()
            self.assertEqual(r.rc, 0)
            column = "post_url" if name == "image" else "post_url_sb"
            self.assertEqual(r.m["set_field"].call_args.args[2:4], (column, URL))

    def test_image_link_write_failure_raises(self) -> None:
        for source in ("native", "playwright"):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, "notion down"):
                run_image(source, write_err=RuntimeError("notion down"))


class DriftTests(unittest.TestCase):
    """A failed link write is no longer swallowed by the video Note (fails on the old copy)."""

    def test_video_link_write_failure_raises(self) -> None:
        for source in ("native", "playwright"):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, "notion down"):
                run_video(source, write_err=RuntimeError("notion down"))

    def test_video_publisher_reuses_the_shared_flow(self) -> None:
        self.assertIs(vn.publish_note_flow, sn.publish_note_flow)


if __name__ == "__main__":
    unittest.main()
