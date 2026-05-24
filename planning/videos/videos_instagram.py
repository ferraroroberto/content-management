"""Instagram (Meta planner) video composer driver for the weekly-video orchestrator.

Walks the Meta Business planner for one clip: planner → hover day column →
Schedule menu → Schedule post → close sub-modal → upload .mp4 → caption
(short) → Set date and time → fill date+time → Schedule. Reuses every
helper from ``planning.instagram.schedule_instagram_posts`` — Meta's
composer accepts .mp4 through the same FileChooser path as images.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.instagram.instagram_session import (  # noqa: E402
    InstagramSession,
    LoginRequiredError,
    load_instagram_config,
)
from planning.instagram.schedule_instagram_posts import (  # noqa: E402
    _assert_composer_media_count,
    _cancel_composer,
    _check_meta_video_error_toast,
    _click_action_button,
    _count_composer_media,
    _delete_extra_media_tiles,
    _dismiss_initial_schedule_submodal,
    _ensure_set_date_toggle_on,
    _fill_post_text,
    _open_day_schedule_menu,
    _set_all_visible_date_time,
    _upload_files,
    _wait_composer_closes,
    dismiss_meta_verified_modal,
    return_to_planner,
    wait_action_button_enabled,
)
from planning.videos.videos_session import ClipPayload  # noqa: E402

logger = logging.getLogger("videos_instagram")


@dataclass
class VideoRow:
    page_id: str
    day: date
    payload: ClipPayload
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return self.day.strftime("%Y%m%d")


def schedule_one_video(
    session: InstagramSession,
    ig_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    _open_day_schedule_menu(page, row.day, "Schedule post")
    _dismiss_initial_schedule_submodal(page)
    # Meta's "Add photo/video" accepts .mp4 — give the same helper a list of one.
    # is_video=True narrows Leg 0 to the video-accept input (issue #37).
    _upload_files(page, [row.payload.video_path], is_video=True)
    # Video transcoding in Meta's composer takes noticeably longer than images.
    # Give it a head-start before we poll, so we don't spin on a button that's
    # been re-rendered by the upload mid-attach.
    page.wait_for_timeout(2000)

    # Issue #37 — duplicate-attach recovery + assertion.
    # When Leg 0's set_input_files times out (Playwright's 4 s default was
    # too tight for a multi-MB video, raised after the file was actually
    # accepted), the legacy fallthrough opened the file chooser and
    # attached the same clip TWICE. Leg 0 now race-guards, but we also
    # defend in depth here: if the composer ends up with extra tiles for
    # any reason, dedupe LIFO before driving the Schedule button.
    pre_dedupe_count = _count_composer_media(page)
    if pre_dedupe_count > 1:
        logger.warning(
            "⚠️ %s IG: composer shows %d media tiles after upload — "
            "auto-deduping to 1 (issue #37).",
            label, pre_dedupe_count,
        )
        final_count = _delete_extra_media_tiles(page, target=1)
        # Give Meta a beat to re-evaluate the error toast / Schedule state.
        page.wait_for_timeout(1500)
        if final_count != 1:
            # Dedupe over-deleted (e.g. 0 left) or could not make progress.
            # Either way scheduling now would create an empty/duplicated
            # post — abort hard so the FAIL handler screenshots the
            # composer instead of driving Schedule.
            raise RuntimeError(
                f"Dedupe ended with {final_count} attached media tile(s) "
                f"(expected 1) — aborting before Schedule click."
            )

    # Hard assertion: any remaining mismatch raises and the existing FAIL
    # handler screenshots the composer for triage.
    _assert_composer_media_count(page, expected=1)
    # Check for Meta's "video too long" / "only one video" toast, which
    # also keeps Schedule disabled. Surface it as an explicit FAIL.
    toast = _check_meta_video_error_toast(page)
    if toast:
        raise RuntimeError(f"Meta rejected the video: {toast!r}")

    _fill_post_text(page, row.payload.caption_short)
    _ensure_set_date_toggle_on(page)
    _set_all_visible_date_time(
        page, row.day,
        video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-ig-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s IG: composer ready, screenshot → %s", label, shot)
        _cancel_composer(page)
        return "IG:DRY"

    # Wait for Meta's transcode to finish — the Schedule button is
    # ``aria-disabled="true"`` while the upload is still being processed.
    # 90 s is comfortably above the typical short-clip transcode time. If
    # it never enables, check the error toast before erroring out so the
    # FAIL detail names the actual Meta cause when present.
    try:
        wait_action_button_enabled(page, "Schedule", timeout_ms=90000)
    except (RuntimeError, PWTimeoutError) as err:
        late_toast = _check_meta_video_error_toast(page)
        if late_toast:
            raise RuntimeError(f"Meta rejected the video: {late_toast!r}") from err
        raise
    _click_action_button(page, "Schedule")
    if not _wait_composer_closes(page, ig_cfg["feed_url"], timeout_ms=30000):
        shot = out_dir / f"{label}-ig-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"IG composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s IG video scheduled", label)
    return "IG:LIVE"


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    if not rows:
        return []
    ig_cfg = load_instagram_config()
    results: list[dict] = []
    with InstagramSession(ig_cfg) as session:
        try:
            session.goto_with_login_check(ig_cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ IG login required: %s", err)
            for row in rows:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return results
        session.page.wait_for_timeout(4500)
        dismiss_meta_verified_modal(session.page)
        session.page.wait_for_timeout(500)

        for row in rows:
            return_to_planner(session.page, ig_cfg["feed_url"])
            try:
                status = schedule_one_video(session, ig_cfg, video_cfg, row, dry_run=dry_run)
                results.append({
                    "day": row.day_title,
                    "status": "DRY" if dry_run else "LIVE",
                    "detail": status,
                })
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-ig-video-error")
                logger.error("❌ IG %s failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_planner(session.page, ig_cfg["feed_url"])
                results.append({
                    "day": row.day_title,
                    "status": "FAIL",
                    "detail": f"{err} (screenshot {shot})",
                })
    return results
