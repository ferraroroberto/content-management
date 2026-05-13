"""Publish a Substack Note from a Notion editorial row.

CLI:
    python -m substack.post_substack_note [--date YYYYMMDD] [--dry-run] [--force] [--debug]

Default date is today (local). The script is idempotent: if the editorial
row's ``post_url`` column is already populated, it exits 0 unless ``--force``
is set.
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

sys.path.append(str(Path(__file__).parent.parent))
from substack.notion_editorial import (  # noqa: E402
    get_field,
    get_property_type,
    get_row_by_day,
    init_notion_client,
    set_field,
)
from substack.substack_session import (  # noqa: E402
    LoginRequiredError,
    SubstackSession,
    configure_logger,
    load_notion_token,
    load_substack_config,
    normalize_day,
)

logger = logging.getLogger("substack_post_note")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a Substack Note from Notion editorial.")
    parser.add_argument("--date", type=str, default=None, help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--dry-run", action="store_true", help="Skip clicking Post; save composer screenshot instead.")
    parser.add_argument("--force", action="store_true", help="Post even if post_url is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def _resolve_image_path(illustrations_folder: str, image_filename: str) -> Path:
    """Join `illustrations_folder` with `image_filename`; tolerate the "a.png, b.png" formula output."""
    if image_filename is None or str(image_filename).strip() == "":
        raise FileNotFoundError("Notion editorial row has no image filename for this date.")
    # The Notion 'ill (copy)' formula can yield ", "-joined names — take the first.
    first = str(image_filename).split(",")[0].strip()
    candidate = Path(illustrations_folder) / first
    if not candidate.exists():
        raise FileNotFoundError(f"Illustration not found: {candidate}")
    return candidate


def _open_note_composer(page) -> None:
    """Click 'Create' → 'Note' to open the dialog. Anchored on ARIA roles."""
    page.get_by_role("button", name=re.compile("create", re.I)).first.click()
    page.get_by_role("menuitem", name=re.compile(r"^note$", re.I)).first.click()


def _fill_note_dialog(page, body_text: str, image_path: Path) -> None:
    """Fill the body and attach the image inside the composer.

    Substack's Note composer is not a true ``role="dialog"`` and the editor is
    a ProseMirror ``contenteditable`` div, not a ``role="textbox"`` — so we
    target by ``contenteditable`` and fall back through several strategies for
    the image upload (hidden file input first, then file-chooser via the image
    button, then OS picker via expect_file_chooser).
    """
    # The editor — prefer the contenteditable whose placeholder mentions "mind",
    # falling back to the last visible one if no placeholder match.
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
    try:
        actual = editor.inner_text(timeout=2000)
    except Exception:
        actual = ""
    logger.debug("✏️ Editor content after fill (%d chars)", len(actual))
    if actual.strip() != body_text.strip():
        logger.warning("⚠️ Editor content does not match body — text may have landed in the wrong element.")

    # Scope a "composer container" = the nearest ancestor of the editor that
    # contains both the Cancel and Post buttons. We hunt for the image upload
    # input only inside this container so we don't accidentally hit the page's
    # avatar / cover-photo / other file inputs.
    composer = editor.locator(
        'xpath=ancestor::*[.//button[normalize-space()="Post"] and .//button[normalize-space()="Cancel"]][1]'
    )
    if composer.count() == 0:
        logger.warning("⚠️ Could not scope composer container; falling back to page-wide search.")
        composer = page.locator("body")

    if _try_attach_image(composer, page, image_path):
        logger.debug("📎 Image attach triggered")
    else:
        raise RuntimeError("Could not attach image — no composer-scoped file input or image button found.")

    # Wait for the *real* upload preview to render — an <img> with naturalWidth > 200
    # inside the composer. (Any small <img> like an avatar would match a naive selector
    # in milliseconds, hence the size filter.)
    try:
        composer_handle = composer.element_handle()
        page.wait_for_function(
            """(el) => {
                if (!el) return false;
                for (const img of el.querySelectorAll('img')) {
                    if (img.complete && img.naturalWidth > 200 && img.naturalHeight > 200) {
                        return true;
                    }
                }
                return false;
            }""",
            arg=composer_handle,
            timeout=30000,
        )
        logger.info("🖼️ Image preview fully loaded in composer.")
    except PWTimeoutError:
        logger.warning("⚠️ Real image preview did not load within 30s — Post may still be disabled.")


def _try_attach_image(composer, page, image_path: Path) -> bool:
    """Attach an image inside the composer container. Returns True on success.

    Order of strategies:
    1. Hidden <input type="file"> already mounted inside the composer.
    2. Click the composer's image button, then catch the file chooser dialog.
    """
    # Strategy 1: scoped to composer.
    file_inputs = composer.locator('input[type="file"]')
    n = file_inputs.count()
    logger.debug("Composer-scoped file inputs found: %d", n)
    if n > 0:
        try:
            file_inputs.first.set_input_files(str(image_path))
            return True
        except Exception as err:
            logger.debug("Composer file input set_input_files failed: %s", err)

    # Strategy 2: composer-scoped image-style buttons + file chooser.
    button_selectors = [
        'button[aria-label*="image" i]',
        'button[aria-label*="photo" i]',
        'button[aria-label*="upload" i]',
        'button[aria-label*="picture" i]',
        'button[title*="image" i]',
        'button[title*="photo" i]',
    ]
    for sel in button_selectors:
        loc = composer.locator(sel)
        if loc.count() == 0:
            continue
        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                loc.first.click()
            fc_info.value.set_files(str(image_path))
            return True
        except Exception as err:
            logger.debug("Image button %s failed: %s", sel, err)
            continue

    # Last resort: scan all file inputs anywhere — but only after composer-scoped failed.
    page_inputs = page.locator('input[type="file"]')
    if page_inputs.count() > 0:
        try:
            logger.debug("Falling back to first page-level file input (composer-scoped search came up empty).")
            page_inputs.first.set_input_files(str(image_path))
            return True
        except Exception as err:
            logger.debug("Page-level file input set_input_files failed: %s", err)

    return False


def _resolve_published_note_url(page, profile_url: str) -> Optional[str]:
    """After posting, navigate to profile and read the topmost note's permalink."""
    page.goto(profile_url, wait_until="domcontentloaded")
    # Top-most activity card. Prefer share-button data-href, fall back to a timestamp anchor.
    candidates = [
        'div[data-component-name*="ActivityCard" i] a[data-href*="/note/"]',
        'div[data-component-name*="ActivityCard" i] a[href*="/note/"]',
        'a[data-href*="/note/"]',
        'a[href*="/note/"]',
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.count() == 0:
                continue
            href = el.get_attribute("data-href") or el.get_attribute("href")
            if href:
                if href.startswith("/"):
                    return f"https://substack.com{href}"
                return href
        except Exception:  # pragma: no cover — selector probing
            continue

    # Fallback: click the latest note and read page.url
    try:
        page.locator('a[href*="/note/"]').first.click()
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        return page.url
    except Exception as err:
        logger.warning("⚠️ Could not resolve note URL via fallback: %s", err)
        return None


def post_note(
    cfg: dict,
    target_day: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    session: Optional[SubstackSession] = None,
) -> int:
    """Core logic. Returns a process-style exit code (0 success, non-zero on failure)."""
    notion = init_notion_client(load_notion_token())
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3

    columns = cfg["notion_columns"]
    row = get_row_by_day(notion, cfg["editorial_db_id"], target_day, columns)
    if row is None:
        logger.error("❌ No editorial row for day=%s — aborting.", target_day)
        return 4

    page_id = row["id"]
    existing_url = get_field(row, "post_url", columns)
    if existing_url and not force:
        logger.info("ℹ️ post_url already populated (%s) — already posted. Skipping (use --force to override).", existing_url)
        return 0

    body_text = get_field(row, "text_body", columns) or ""
    if not str(body_text).strip():
        logger.error("❌ Editorial row has empty body text — refusing to post empty Note.")
        return 5

    image_filename = get_field(row, "image_filename", columns)
    image_path = _resolve_image_path(cfg["illustrations_folder"], image_filename)
    logger.info("🖼️ Using image: %s", image_path)
    logger.info("📝 Body length: %d chars", len(str(body_text)))

    owned_session = session is None
    s = session or SubstackSession(cfg)
    if owned_session:
        s.__enter__()

    try:
        try:
            s.goto_with_login_check(cfg["profile_url"])
        except LoginRequiredError as err:
            logger.error("❌ %s", err)
            return 6

        try:
            _open_note_composer(s.page)
        except Exception as err:
            s.screenshot_failure(f"{target_day}-selector-fail")
            logger.error("❌ Could not open Note composer: %s", err)
            return 7

        try:
            _fill_note_dialog(s.page, str(body_text), image_path)
        except Exception as err:
            s.screenshot_failure(f"{target_day}-fill-fail")
            logger.error("❌ Could not fill the Note dialog: %s", err)
            return 8

        if dry_run:
            out_dir = Path(__file__).resolve().parent.parent / "results" / "substack"
            out_dir.mkdir(parents=True, exist_ok=True)
            # Tight viewport-only shot of the composer (full_page=False so we see what the user sees).
            shot = out_dir / f"{target_day}-dryrun.png"
            s.page.screenshot(path=str(shot), full_page=False)
            logger.info("✅ DRY-RUN: composer screenshot saved → %s (no post was published)", shot)
            return 0

        try:
            s.page.get_by_role("dialog").first.get_by_role(
                "button", name=re.compile(r"^post$", re.I)
            ).first.click()
            s.page.get_by_role("dialog").first.wait_for(state="hidden", timeout=30000)
            logger.info("✅ Note dialog closed — post submitted.")
        except Exception as err:
            s.screenshot_failure(f"{target_day}-post-fail")
            logger.error("❌ Could not click Post / dialog did not close: %s", err)
            return 9

        note_url = _resolve_published_note_url(s.page, cfg["profile_url"])
        if not note_url:
            logger.warning("⚠️ Note appears published but URL could not be resolved.")
            return 10
        logger.info("🔗 Published note URL: %s", note_url)

        prop_type = get_property_type(row, "post_url", columns)
        set_field(notion, page_id, "post_url", note_url, columns, prop_type)
        logger.info("✅ Wrote post_url to Notion editorial row.")
        return 0

    finally:
        if owned_session:
            s.__exit__(None, None, None)


def main() -> int:
    args = parse_args()
    configure_logger("substack_post_note", debug=args.debug)
    cfg = load_substack_config()
    target_day = normalize_day(args.date)
    dry_run = args.dry_run or (cfg.get("dry_run_default", False) and not args.force)
    logger.info("🚀 Substack Note publish — day=%s dry_run=%s force=%s", target_day, dry_run, args.force)
    return post_note(cfg, target_day, dry_run=dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
