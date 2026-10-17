"""Publish a Substack Note from a Notion editorial row.

CLI:
    python -m planning.substack.post_substack_note [--date YYYYMMDD] [--dry-run] [--force] [--debug]

Default date is today (local). The script is idempotent: if the editorial
row's ``post_url`` column is already populated, it exits 0 unless ``--force``
is set.

**Two publish backends**, chosen by ``substack.note_source`` in ``config.json``
(same pattern as the reporting pipeline's ``source`` flag, and the same one-key
rollback):

* ``"playwright"`` (default) — drives the real-Chrome Note composer.
* ``"native"`` — posts over Substack's HTTP API with the harvested cookie (no
  browser). Also *exact*: it writes the permalink of the note it just created,
  where the Playwright path re-reads the profile afterwards and takes whatever
  is topmost.

Everything either side of publishing — the Notion read, the idempotence guard,
the empty-body refusal, the image resolution and the ``post_url`` write-back —
is shared, so the two backends differ only in how the Note reaches Substack.
Video notes (see ``post_substack_video_note.py``) support the same
``note_source`` flag (issue #189) and publish through the same
``publish_note_flow``: each publisher passes a ``NotePublish`` with its own
fill step, Post-ready wait and link column (issue #371).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    get_property_type,
    get_row_by_day,
    init_notion_client,
    set_field,
)
from planning._session_base import results_dir  # noqa: E402
from planning._wip_rows import resolve_image_path  # noqa: E402
from planning.substack.substack_session import (  # noqa: E402
    LoginRequiredError,
    SubstackSession,
    configure_logger,
    load_notion_token,
    load_substack_config,
    normalize_day,
)

logger = logging.getLogger("substack_post_note")

# Exit code: the Note is live on Substack but its link column is still empty.
# Kept apart from a failed post (6–10) and from the daily pipeline's rc 99 (a
# branch raised): nothing needs re-posting, but the link must be filled in by
# hand, or the next run will post the Note a second time.
RC_POSTED_LINK_NOT_WRITTEN = 11


def _publish_native(cfg: dict, body_text: str, image_path: Path) -> str:
    """Publish the Note over the HTTP API; return its permalink.

    Raises rather than returning ``None`` — every failure mode here (expired
    cookie, upload rejected, no resolvable handle) is a real error the caller
    turns into a non-zero exit code.

    Imported lazily so the Playwright path never pays for it and a missing
    ``api_session.json`` can't break the default backend.
    """
    from planning.substack.api_client import (
        fetch_self_handle,
        note_permalink,
        publish_note,
    )

    note = publish_note(body_text, image_path=image_path)
    # The create response does NOT echo the handle (verified live), so fall back
    # to config and then to the API — the same source the reporting pipeline
    # builds its post_id from, so the two can't disagree.
    handle = note.get("handle") or cfg.get("handle") or fetch_self_handle()
    return note_permalink(handle, note["id"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a Substack Note from Notion editorial.")
    parser.add_argument("--date", type=str, default=None, help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--dry-run", action="store_true", help="Skip clicking Post; save composer screenshot instead.")
    parser.add_argument("--force", action="store_true", help="Post even if post_url is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def _open_note_composer(page) -> None:
    """Click 'Create' → 'Note' to open the dialog. Anchored on ARIA roles."""
    page.get_by_role("button", name=re.compile("create", re.I)).first.click()
    page.get_by_role("menuitem", name=re.compile(r"^note$", re.I)).first.click()


# Ancestor of the editor that contains both the Cancel and Post buttons — used
# to scope file-input / upload-button searches so they don't accidentally hit
# the page's avatar / cover-photo / other file inputs. Shared between the
# image-Note and video-Note publishers (identical composer DOM shape).
COMPOSER_SCOPE_XPATH = (
    'xpath=ancestor::*[.//button[normalize-space()="Post"] and .//button[normalize-space()="Cancel"]][1]'
)


def _locate_note_editor(page, body_text: str):
    """Find the Note composer's contenteditable editor, wait for it, click, and fill.

    Substack's Note composer is not a true ``role="dialog"`` and the editor is
    a ProseMirror ``contenteditable`` div, not a ``role="textbox"`` — so we
    target by ``contenteditable``, preferring the one whose placeholder
    mentions "mind" (Substack's "What's on your mind?" prompt) and falling
    back to the last visible one if no placeholder match. Shared between the
    image-Note and video-Note publishers — same DOM shape either way.
    """
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
    return editor


def _fill_note_dialog(page, body_text: str, image_path: Path) -> None:
    """Fill the body and attach the image inside the composer.

    See :func:`_locate_note_editor` for how the composer's editor is found;
    the image upload (hidden file input first, then file-chooser via the
    image button, then OS picker via expect_file_chooser) is attempted only
    inside the composer-scoped container.
    """
    editor = _locate_note_editor(page, body_text)
    try:
        actual = editor.inner_text(timeout=2000)
    except Exception:
        actual = ""
    logger.debug("✏️ Editor content after fill (%d chars)", len(actual))
    if actual.strip() != body_text.strip():
        logger.warning("⚠️ Editor content does not match body — text may have landed in the wrong element.")

    composer = editor.locator(COMPOSER_SCOPE_XPATH)
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


@dataclass(frozen=True)
class NotePublish:
    """What differs between the image Note and the video Note publish.

    ``publish_note_flow`` owns the sequence; each publisher supplies only its
    payload-specific steps. ``noun`` ("Note" / "video Note") words the log
    lines; ``failure_label`` prefixes the failure screenshots.
    """

    noun: str
    failure_label: str
    dry_run_shot: str
    native_dry_run: str
    publish_native: Callable[[], str]
    fill: Callable[[Any], None]
    click_post: Callable[[Any], None]
    write_link: Callable[[str], None]
    wait_post_ready: Optional[Callable[[Any], None]] = None
    close_timeout_ms: int = 30000


def _click_post_button(page) -> None:
    """Click the image Note composer's 'Post' button, anchored on its name."""
    page.get_by_role("dialog").first.get_by_role(
        "button", name=re.compile(r"^post$", re.I)
    ).first.click()


def _write_link(note: NotePublish, note_url: str) -> int:
    """Record a published Note's link; ``RC_POSTED_LINK_NOT_WRITTEN`` if that fails."""
    try:
        note.write_link(note_url)
    except Exception as err:  # noqa: BLE001
        logger.error(
            "🔗❌ POSTED, LINK NOT WRITTEN: the %s is live at %s but its link "
            "column could not be written (%s). Fill it in by hand, or the next "
            "run will post it again.",
            note.noun, note_url, err,
        )
        return RC_POSTED_LINK_NOT_WRITTEN
    return 0


def publish_note_flow(
    cfg: dict,
    note: NotePublish,
    *,
    dry_run: bool,
    session: Optional[SubstackSession] = None,
) -> int:
    """Publish one Note through the configured backend; return an exit code.

    The one publish sequence behind both the image Note and the video Note
    (issue #371): backend check, native dry run / publish, or open composer →
    fill → dry-run screenshot → optional Post-ready wait → click Post →
    resolve the URL → write the link column.

    ``session`` is opened here when not given, and only on the Playwright
    path — the native backend never needs a browser. A Note that posted but
    could not record its link returns ``RC_POSTED_LINK_NOT_WRITTEN``: an empty
    link column makes the next run post it again, so it must alert, but it is
    not a failed post.
    """
    note_source = str(cfg.get("note_source", "playwright")).lower()
    if note_source not in ("playwright", "native"):
        logger.error(
            "❌ Unknown substack.note_source %r — expected 'playwright' or 'native'.",
            note_source,
        )
        return 2

    if note_source == "native":
        if dry_run:
            logger.info("✅ DRY-RUN (native): would publish %s — nothing was posted.",
                        note.native_dry_run)
            return 0
        logger.info("🔌 Publishing the %s via the native HTTP API (no browser).", note.noun)
        try:
            note_url = note.publish_native()
        except Exception as err:  # noqa: BLE001
            logger.error("❌ Native %s publish failed: %s", note.noun.lower(), err)
            return 9
        logger.info("🔗 Published %s URL: %s", note.noun.lower(), note_url)
        return _write_link(note, note_url)

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
            s.screenshot_failure(f"{note.failure_label}-selector-fail")
            logger.error("❌ Could not open Note composer: %s", err)
            return 7

        try:
            note.fill(s.page)
        except Exception as err:
            s.screenshot_failure(f"{note.failure_label}-fill-fail")
            logger.error("❌ Could not fill the %s dialog: %s", note.noun, err)
            return 8

        if dry_run:
            out_dir = results_dir("substack")
            out_dir.mkdir(parents=True, exist_ok=True)
            # Tight viewport-only shot of the composer (full_page=False so we see what the user sees).
            shot = out_dir / note.dry_run_shot
            s.page.screenshot(path=str(shot), full_page=False)
            logger.info("✅ DRY-RUN: %s composer screenshot saved → %s (no post was published)",
                        note.noun, shot)
            return 0

        if note.wait_post_ready is not None:
            try:
                note.wait_post_ready(s.page)
            except PWTimeoutError:
                s.screenshot_failure(f"{note.failure_label}-post-disabled")
                logger.error("❌ Substack Post button never enabled — the upload "
                             "likely did not finish in time.")
                return 9

        try:
            note.click_post(s.page)
            s.page.get_by_role("dialog").first.wait_for(state="hidden", timeout=note.close_timeout_ms)
            logger.info("✅ Note dialog closed — %s submitted.", note.noun)
        except Exception as err:
            s.screenshot_failure(f"{note.failure_label}-post-fail")
            logger.error("❌ Could not click Post / dialog did not close: %s", err)
            return 9

        note_url = _resolve_published_note_url(s.page, cfg["profile_url"])
        if not note_url:
            logger.warning("⚠️ %s appears published but URL could not be resolved.", note.noun)
            return 10
        logger.info("🔗 Published %s URL: %s", note.noun.lower(), note_url)
        return _write_link(note, note_url)

    finally:
        if owned_session:
            s.__exit__(None, None, None)


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
    image_path = resolve_image_path(
        cfg["illustrations_folder"],
        image_filename,
        missing="Notion editorial row has no image filename for this date.",
    )
    logger.info("🖼️ Using image: %s", image_path)
    logger.info("📝 Body length: %d chars", len(str(body_text)))

    def write_link(note_url: str) -> None:
        prop_type = get_property_type(row, "post_url", columns)
        set_field(notion, page_id, "post_url", note_url, columns, prop_type)
        logger.info("✅ Wrote post_url to Notion editorial row.")

    body = str(body_text)
    return publish_note_flow(
        cfg,
        NotePublish(
            noun="Note",
            failure_label=target_day,
            dry_run_shot=f"{target_day}-dryrun.png",
            native_dry_run=f"{len(body)} chars + {image_path.name}",
            publish_native=lambda: _publish_native(cfg, body, image_path),
            fill=lambda page: _fill_note_dialog(page, body, image_path),
            click_post=_click_post_button,
            write_link=write_link,
        ),
        dry_run=dry_run,
        session=session,
    )

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
