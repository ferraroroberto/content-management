"""Getting media files into the Meta planner's post / story composer.

``upload_files`` tries each way Meta has shipped its "Add photo/video"
affordance in turn (a pre-mounted file input, a direct file chooser, the
intermediate "Upload from computer" dialog, any attached file input) and
waits for the previews to render. Split out of ``schedule_instagram_posts``
so that module stays under the structure ratchet's file-size limit; the
selectors themselves live in ``instagram_labels``.

Logs to the ``instagram_schedule`` logger, like the scheduler that calls it,
so a videos run that enables that logger still sees every 📤 Leg N line and
debug-screenshot path.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from planning._session_base import results_dir
from planning.instagram.instagram_labels import (
    ADD_MEDIA_BTN_SELECTOR,
    UPLOAD_FROM_COMPUTER_SELECTOR,
)

logger = logging.getLogger("instagram_schedule")


def _dump_upload_debug(page: Page, tag: str) -> Path:
    """Save a screenshot + log the visible buttons in any open dialog.

    Called from ``upload_files`` when an upload strategy fails so the next
    triage doesn't need a live debug session. The returned path is included
    in the raised exception message.
    """
    out_dir = results_dir("instagram")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shot = out_dir / f"{ts}-upload-debug-{tag}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception as err:
        logger.debug("Could not save upload debug screenshot: %s", err)
        return shot
    try:
        # Dump up to 12 visible button-like elements in the latest dialog so
        # the log line tells us what affordances Meta is offering at the
        # moment of failure.
        visible_buttons = page.evaluate(
            r"""
            () => {
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
                const root = dialogs.length ? dialogs[dialogs.length - 1] : document;
                const buttons = Array.from(root.querySelectorAll('div[role="button"], button'));
                return buttons
                    .filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    })
                    .slice(0, 12)
                    .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 60));
            }
            """
        )
        logger.debug(
            "🔎 Upload-debug (%s) — visible buttons in latest dialog: %s",
            tag, visible_buttons,
        )
    except Exception as err:
        logger.debug("Could not enumerate dialog buttons: %s", err)
    return shot


def _upload_settle_ms(n_files: int) -> int:
    """Settle time after an upload, so Meta can render the preview thumbnails.

    A stated guess from the first scheduler commit (a flat 3 s plus 1.5 s per
    file), never measured.
    """
    return 3000 + 1500 * n_files


def _upload_leg0(page: Page, file_paths: list[str], paths: list[Path], *, is_video: bool) -> bool:
    """Send the files through a pre-mounted input; True when they are attached."""
    # --- Leg 0: pre-mounted hidden input[type=file] (Meta planner often uses
    #            a label-wrapped <input> where the visible button is decorative).
    # When uploading a video, narrow to the video-accept input so we do not
    # accidentally fill an image input that Meta also mounts in the same
    # composer.
    leg0_selector = (
        'input[type="file"][accept*="video"]' if is_video else 'input[type="file"]'
    )
    pre_inp = page.locator(leg0_selector).last
    leg0_n_inputs = pre_inp.count() if pre_inp else 0
    if leg0_n_inputs > 0:
        # 15 s — set_input_files on a multi-MB video can blow past 4 s while
        # Meta is still accepting the file. A premature TimeoutError there
        # used to throw us into Leg 1, which opened the file chooser and
        # attached the same clip a SECOND time (a duplicate attachment).
        try:
            pre_inp.set_input_files(file_paths, timeout=15000)
            logger.info(
                "📤 Leg 0 (%s, %d input(s) matched): %d file(s) sent via "
                "pre-mounted input[type=file] (no Add-photo/video click needed).",
                leg0_selector, leg0_n_inputs, len(file_paths),
            )
            page.wait_for_timeout(_upload_settle_ms(len(paths)))
            return True
        except Exception as err:
            logger.warning(
                "⚠️ Leg 0 (%s) raised after %d input(s) matched: %s — "
                "probing composer before fallthrough (issue #37 race guard).",
                leg0_selector, leg0_n_inputs, err,
            )
            # Race guard: the input may have ACCEPTED the file before
            # Playwright timed out. Falling through to Leg 1 in that case
            # double-attaches. Probe the composer; if any media is already
            # attached, treat Leg 0 as a success.
            try:
                already_attached = _count_composer_media(page)
            except Exception as probe_err:
                logger.debug("Race-guard probe failed: %s", probe_err)
                already_attached = 0
            if already_attached > 0:
                logger.info(
                    "📤 Leg 0 (%s): file appears attached despite timeout "
                    "(probe sees %d media tile(s)) — not falling through.",
                    leg0_selector, already_attached,
                )
                page.wait_for_timeout(_upload_settle_ms(len(paths)))
                return True
    return False


def _upload_leg1(page: Page, add_btn, file_paths: list[str], paths: list[Path]) -> bool:
    """Click 'Add photo/video' for a direct file chooser; True when the files were sent."""
    # --- Leg 1: click 'Add photo/video' and hope for a direct file chooser ---
    # Meta's post composer sometimes leaves the button without a hydrated
    # click handler for a short window after the sub-modal closes.
    # Settle, click, and on miss retry once via a JS-native click — that
    # bypasses any React handler-binding race a Playwright dispatch hits.
    page.wait_for_timeout(1500)

    def _try_click_for_chooser(click_fn) -> Optional[object]:
        try:
            with page.expect_file_chooser(timeout=6000) as fc_info:
                click_fn()
            return fc_info.value
        except Exception as err:
            logger.debug("Add-photo/video click → no FileChooser: %s", err)
            return None

    def _playwright_click() -> None:
        try:
            add_btn.click(timeout=5000)
        except Exception:
            # Pointer-events block: bypass via force.
            add_btn.click(force=True, timeout=5000)

    def _js_click() -> None:
        # Dispatched directly on the DOM node — bypasses Playwright's overlay
        # / pointer-events checks and any half-bound React handler.
        add_btn.evaluate("el => el.click()")

    leg1_chooser = _try_click_for_chooser(_playwright_click)
    if leg1_chooser is None:
        # Re-find: the previous click may have changed the DOM even if no
        # chooser fired. Then JS-click the freshly resolved element.
        page.wait_for_timeout(600)
        add_btn = page.locator(ADD_MEDIA_BTN_SELECTOR).first
        leg1_chooser = _try_click_for_chooser(_js_click)

    if leg1_chooser is not None:
        leg1_chooser.set_files(file_paths)
        logger.info("📤 Leg 1: %d file(s) sent via direct FileChooser.", len(file_paths))
        page.wait_for_timeout(_upload_settle_ms(len(paths)))
        return True
    return False


def _upload_leg2(page: Page, file_paths: list[str], paths: list[Path]) -> bool:
    """Use the intermediate dialog's 'Upload from computer'; True when the files were sent."""
    # --- Leg 2: intermediate dialog — click 'Upload from computer' inside it ---
    leg2_chooser = None
    upload_btn = page.locator(UPLOAD_FROM_COMPUTER_SELECTOR).first
    try:
        upload_btn.wait_for(state="visible", timeout=5000)
    except Exception as err:
        logger.debug("Leg 2 ('Upload from computer' button not present): %s", err)
    else:
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                try:
                    upload_btn.click(timeout=5000)
                except Exception:
                    upload_btn.click(force=True, timeout=5000)
            leg2_chooser = fc_info.value
        except Exception as err:
            logger.debug("Leg 2 (FileChooser after Upload-from-computer): %s", err)

    if leg2_chooser is not None:
        leg2_chooser.set_files(file_paths)
        logger.debug(
            "📤 Leg 2: %d file(s) sent via FileChooser after intermediate dialog.",
            len(file_paths),
        )
        page.wait_for_timeout(_upload_settle_ms(len(paths)))
        return True
    return False


def upload_files(page: Page, paths: list[Path], *, is_video: bool = False) -> None:
    """Get files into the Meta composer.

    Meta's "Add photo/video" affordance has shipped in two shapes:

    1. **Direct file chooser** (story path, older post path): clicking it
       opens the OS file picker; ``page.expect_file_chooser`` intercepts
       the FileChooser event and we push files via ``set_files``.
    2. **Intermediate dialog** (current post path on most accounts, issue
       #28): clicking it opens a media dialog whose "Upload from computer"
       button is what actually triggers the file chooser.

    We try (1) first; if no FileChooser appears within 8 s, we look for the
    "Upload from computer" button inside the newly-opened dialog and try
    (2). If neither produces a FileChooser, we fall back to any attached
    ``input[type=file]`` (8 s wait). Each leg logs which one ran, and on
    final failure we save a debug screenshot + dump the visible dialog
    buttons so the next regression triages from artefacts, not a live DOM
    session (AC #5).

    ``is_video=True`` narrows Leg 0's selector to
    ``input[type=file][accept*="video"]`` so we cannot accidentally bind
    files to an image input when Meta's post composer mounts both
    (issue #37 — duplicated attach when the video input is filled AND the
    image affordance re-opens its own chooser).
    """
    file_paths = [str(p) for p in paths]

    add_btn = page.locator(ADD_MEDIA_BTN_SELECTOR).first
    try:
        add_btn.wait_for(state="visible", timeout=12000)
    except Exception as err:
        raise RuntimeError(f"'Add photo/video' button never appeared: {err}")

    # Diagnostic: dump the add-button's outer HTML so we can see whether
    # Meta wired a click handler or expects a pre-mounted input[type=file]
    # behind it. Cheap; only logged at DEBUG.
    try:
        outer = add_btn.evaluate("el => el.outerHTML")
        logger.debug("🔎 Add-media button outerHTML (first 600 chars): %s",
                     (outer or "")[:600])
    except Exception as err:
        logger.debug("Could not snapshot add-media button HTML: %s", err)

    if _upload_leg0(page, file_paths, paths, is_video=is_video):
        return

    if _upload_leg1(page, add_btn, file_paths, paths):
        return

    if _upload_leg2(page, file_paths, paths):
        return

    # --- Leg 3: an input[type=file] is already attached somewhere ---
    inp = page.locator('input[type="file"]').last
    try:
        inp.wait_for(state="attached", timeout=8000)
        inp.set_input_files(file_paths)
        logger.debug("📤 Leg 3: %d file(s) sent via attached input[type=file].", len(file_paths))
        page.wait_for_timeout(_upload_settle_ms(len(paths)))
        return
    except Exception as err:
        logger.debug("Leg 3 (attached input[type=file]): %s", err)
        shot = _dump_upload_debug(page, "all-legs-failed")
        raise RuntimeError(
            f"Could not upload files {paths!r}: all three upload strategies "
            f"failed (direct FileChooser, intermediate dialog → 'Upload from "
            f"computer', attached input[type=file]). Last error from leg 3: "
            f"{err}. Debug screenshot: {shot}"
        )


# JS probe that counts attached media tiles inside the post composer.
#
# Verified live (see E:\\tmp\\probe_meta_composer.py results, May 2026):
#
# * Meta's planner does NOT wrap the post composer in [role="dialog"];
#   the composer renders inline in the planner shell. So we scan the
#   whole document — there is no other "Remove video" / "Remove photo"
#   affordance on the planner page to conflict with this.
# * The trash icon on each attached tile has NO aria-label. The visible
#   text inside its <div role="button"> is exactly "Remove video"
#   (followed by a zero-width space + newline). Same shape for the
#   image flow ("Remove photo") and the ES build ("Eliminar vídeo").
# * Counting <video> elements is WRONG: the right-side "Instagram Feed
#   preview" pane mounts its own <video>, so videos.length = N_attached
#   + 1. Using it as a fallback over-deleted in the live probe.
#
# Therefore the canonical signal is: count <div role="button"> whose
# innerText starts with Remove/Delete/Eliminar/Quitar followed by the
# media noun. Exactly one per attached tile.
_MEDIA_COUNT_JS = r"""
() => {
    const labelRe = /^(remove|delete|eliminar|quitar)\s+(video|photo|media|attachment|file|image|v[ií]deo|foto)\b/i;
    const buttons = Array.from(document.querySelectorAll('[role="button"], button'));
    return buttons.filter(b => labelRe.test(((b.innerText || '') + '').trim())).length;
}
"""


def _count_composer_media(page: Page) -> int:
    """Best-effort count of attached media tiles in the open composer.

    Returns 0 on probe failure so callers can decide whether 0 means
    "empty" or "stale selector"; never raises.
    """
    try:
        return int(page.evaluate(_MEDIA_COUNT_JS) or 0)
    except Exception as err:
        logger.debug("composer media-count probe raised: %s", err)
        return 0
