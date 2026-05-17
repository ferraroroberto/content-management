"""Publish a Substack VIDEO Note from a Notion editorial row's video clip.

Triggered from the daily Substack pipeline on video days (rows where
``Work in Progress Video`` is checked AND ``clip SB(v)`` is populated).
Resolves the shared clip page once, then drives the same Note composer
used by ``post_substack_note`` — but uses the camera/video toolbar icon
(screenshot 4) instead of the image icon, and uploads the .mp4 from the
clip's ``clipPC``/``filePC`` properties. On success, writes
``link SB(v)`` on the editorial row so the videos orchestrator's next run
can untick the shared ``Work in Progress Video`` checkbox.

The video Note's caption is the clip page's ``Text`` property (the same
short caption that feeds IG/TW/TH).

CLI (standalone — same flags shape as ``post_substack_note``):

    python -m planning.substack.post_substack_video_note \\
        [--date YYYYMMDD]  [--dry-run] [--force] [--debug]

When run inside ``planning.substack.daily_pipeline``, call
``post_video_note_if_applicable()`` which returns ``None`` for non-video
days (caller falls back to the image-Note flow).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.substack.post_substack_note import (  # noqa: E402
    _open_note_composer,
    _resolve_published_note_url,
)
from planning.substack.substack_session import (  # noqa: E402
    LoginRequiredError,
    SubstackSession,
    configure_logger,
    load_notion_token,
    load_substack_config,
    normalize_day,
)
from planning.videos.videos_session import (  # noqa: E402
    load_clip_payload,
    load_videos_config,
)
from reporting.notion.editorial import (  # noqa: E402
    get_row_by_day,
    init_notion_client,
    set_field,
)

logger = logging.getLogger("substack_post_video_note")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a Substack VIDEO Note from Notion editorial (weekly video day)."
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip clicking Post; save composer screenshot instead.")
    parser.add_argument("--force", action="store_true",
                        help="Post even if link SB(v) is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def _attach_video_via_composer(composer, page, video_path: Path) -> bool:
    """Attach an .mp4 inside the Substack composer container. Returns True on success.

    Substack's Note composer pre-mounts TWO hidden file inputs (probe-confirmed):

    * ``input[type=file] accept="image/*,.heic"`` -- image upload
    * ``input[type=file] accept="video/*"``       -- video upload

    The visible image/video toolbar buttons have NO aria-label, NO title,
    NO data-testid; only their position distinguishes them (both are 40x40
    SVG-only buttons). The robust approach is to bypass the visible
    buttons entirely and push the .mp4 directly at the ``accept="video/*"``
    input. The earlier version grabbed ``.first`` -- which is the IMAGE
    input -- and Substack silently rejected the .mp4: the note posted
    text-only and the URL inside the body got auto-embedded as a
    link-preview card (screenshot 5).
    """
    video_inputs = composer.locator('input[type="file"][accept*="video"]')
    if video_inputs.count() > 0:
        try:
            video_inputs.first.set_input_files(str(video_path))
            logger.debug("Pushed .mp4 into composer-scoped video file input.")
            return True
        except Exception as err:
            logger.debug("Composer video input set_input_files failed: %s", err)

    page_video_inputs = page.locator('input[type="file"][accept*="video"]')
    if page_video_inputs.count() > 0:
        try:
            page_video_inputs.first.set_input_files(str(video_path))
            logger.debug("Pushed .mp4 into page-level video file input.")
            return True
        except Exception as err:
            logger.debug("Page-level video input set_input_files failed: %s", err)

    return False


def _fill_video_note_dialog(page, body_text: str, video_path: Path) -> None:
    """Fill the body and attach the .mp4 inside the open Note composer."""
    editors = page.locator('[contenteditable="true"]')
    editor = None
    for i in range(editors.count()):
        el = editors.nth(i)
        try:
            placeholder = (
                el.get_attribute("data-placeholder")
                or el.get_attribute("aria-placeholder")
                or el.get_attribute("placeholder")
                or ""
            )
        except Exception:
            placeholder = ""
        if "mind" in placeholder.lower():
            editor = el
            break
    if editor is None:
        editor = editors.last
    editor.wait_for(state="visible", timeout=20000)
    editor.click()
    editor.fill(body_text)

    composer = editor.locator(
        'xpath=ancestor::*[.//button[normalize-space()="Post"] and .//button[normalize-space()="Cancel"]][1]'
    )
    if composer.count() == 0:
        logger.warning("⚠️ Could not scope composer container; falling back to page-wide search.")
        composer = page.locator("body")

    if not _attach_video_via_composer(composer, page, video_path):
        raise RuntimeError("Could not attach video — no video-icon button or composer-scoped file input found.")

    # Wait for the video preview / processing UI to render. Substack shows a
    # <video> element with a real src once upload completes.
    try:
        composer_handle = composer.element_handle()
        page.wait_for_function(
            """(el) => {
                if (!el) return false;
                for (const v of el.querySelectorAll('video')) {
                    if (v.readyState >= 1 || (v.src && v.src.length > 0)) return true;
                }
                return false;
            }""",
            arg=composer_handle,
            timeout=180000,
        )
        logger.info("🎬 Video preview rendered in composer.")
    except PWTimeoutError:
        logger.warning("⚠️ Video preview did not render within 180s — Post may still be disabled.")


def _post_video_note_for_row(
    cfg: dict,
    videos_cfg: dict,
    notion_token: str,
    row: dict,
    *,
    dry_run: bool,
    force: bool,
    session: SubstackSession,
) -> int:
    """Core driver. Returns 0 on success / dry-run, non-zero exit code on failure.

    Caller owns the SubstackSession (so it can be shared with other daily steps).
    """
    notion = init_notion_client(notion_token)
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3

    video_cols = videos_cfg["editorial_columns"]
    clip_cols = videos_cfg["clip_columns"]

    page_id = row["id"]
    props = row.get("properties", {})

    # Idempotency: link SB(v) populated?
    link_sb_col = video_cols.get("post_url_sb")
    link_sb = (props.get(link_sb_col, {}) or {}).get("url") if link_sb_col else None
    if link_sb and not force:
        logger.info("ℹ️ link SB(v) already populated (%s) — skipping (use --force to re-post).", link_sb)
        return 0

    try:
        payload = load_clip_payload(notion, row, video_cols, clip_cols)
    except (RuntimeError, FileNotFoundError) as err:
        logger.error("❌ Clip payload resolution failed: %s", err)
        return 8

    if not payload.caption_short:
        logger.error("❌ Clip page has empty 'Text' caption — refusing to post empty Note.")
        return 5

    try:
        session.goto_with_login_check(cfg["profile_url"])
    except LoginRequiredError as err:
        logger.error("❌ %s", err)
        return 6

    try:
        _open_note_composer(session.page)
    except Exception as err:
        session.screenshot_failure(f"{payload.title or 'video'}-selector-fail")
        logger.error("❌ Could not open Note composer: %s", err)
        return 7

    try:
        _fill_video_note_dialog(session.page, payload.caption_short, payload.video_path)
    except Exception as err:
        session.screenshot_failure(f"{payload.title or 'video'}-fill-fail")
        logger.error("❌ Could not fill the video Note dialog: %s", err)
        return 8

    if dry_run:
        out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "substack"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        shot = out_dir / f"{ts}-video-dryrun.png"
        session.page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN: video composer screenshot → %s (no post was published)", shot)
        return 0

    try:
        session.page.get_by_role("dialog").first.get_by_role(
            "button", name=re.compile(r"^post$", re.I)
        ).first.click()
        session.page.get_by_role("dialog").first.wait_for(state="hidden", timeout=120000)
        logger.info("✅ Note dialog closed — video post submitted.")
    except Exception as err:
        session.screenshot_failure(f"{payload.title or 'video'}-post-fail")
        logger.error("❌ Could not click Post / dialog did not close: %s", err)
        return 9

    note_url = _resolve_published_note_url(session.page, cfg["profile_url"])
    if not note_url:
        logger.warning("⚠️ Video note appears published but URL could not be resolved.")
        return 10
    logger.info("🔗 Published video note URL: %s", note_url)

    try:
        set_field(notion, page_id, "post_url_sb", note_url, video_cols, "url")
        sb_col = video_cols.get("post_url_sb", "link SB")
        logger.info("✅ Wrote %s on editorial row.", sb_col)
    except Exception as err:
        logger.warning("⚠️ Posted OK but failed to write SB post URL: %s", err)

    return 0


def post_video_note_if_applicable(
    target_day: str,
    *,
    dry_run: bool,
    force: bool,
    session: SubstackSession,
) -> Optional[int]:
    """Detect a video day and post the video Note if applicable.

    Returns:
        ``None`` if the day is not a video day (caller should run the
        normal image-note flow).
        Integer exit code if the day was processed (0 on success, non-zero
        on failure).

    Conditions for "video day":
      - An editorial row exists for ``target_day``.
      - That row's ``Work in Progress Video`` checkbox is True.
      - That row's ``clip SB(v)`` relation is populated.
    """
    sub_cfg = load_substack_config()
    videos_cfg = load_videos_config()
    notion_token = load_notion_token()

    notion = init_notion_client(notion_token)
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3

    video_cols = videos_cfg["editorial_columns"]
    row = get_row_by_day(notion, videos_cfg["editorial_db_id"], target_day, video_cols)
    if row is None:
        return None

    props = row.get("properties", {})
    wip_prop = props.get(video_cols["wip_checkbox"], {})
    is_wip_video = bool(wip_prop.get("checkbox", False))

    # Detection: the editorial DB doesn't (yet) have a dedicated `clip SB(v)`
    # relation, so we infer a video day from WIP-Vd checked AND any of the
    # populated per-platform clip relations (LI / IG / TW). The video Note
    # uses the SAME clip page all four platforms share.
    has_any_clip = False
    for p in ("li", "ig", "tw"):
        col = video_cols.get(f"clip_rel_{p}")
        rels = (props.get(col, {}) or {}).get("relation", []) or [] if col else []
        if rels:
            has_any_clip = True
            break

    if not is_wip_video or not has_any_clip:
        return None

    logger.info("📹 %s is a video day for Substack — posting video Note.", target_day)
    return _post_video_note_for_row(
        sub_cfg, videos_cfg, notion_token, row,
        dry_run=dry_run, force=force, session=session,
    )


def main() -> int:
    args = parse_args()
    configure_logger("substack_post_video_note", debug=args.debug)
    sub_cfg = load_substack_config()
    target_day = normalize_day(args.date)
    dry_run = args.dry_run or (sub_cfg.get("dry_run_default", False) and not args.force)
    logger.info("🚀 Substack VIDEO Note publish — day=%s dry_run=%s force=%s",
                target_day, dry_run, args.force)

    with SubstackSession(sub_cfg) as session:
        rc = post_video_note_if_applicable(
            target_day, dry_run=dry_run, force=args.force, session=session,
        )
        if rc is None:
            logger.info("ℹ️ %s is not a video day (no WIP-Video row or no clip SB(v)). Nothing to do.",
                        target_day)
            return 0
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
