"""LinkedIn composer helpers shared between the photo, video, post, and
carousel flows.

This module owns the two patterns that are identical across every LI
scheduler (photo + video + posts-DB + carousel-PDF) but were originally
written inside ``planning/videos/videos_linkedin.py``:

* ``_fill_caption_with_mentions`` — type a caption into LI's composer
  while resolving every ``@FirstName Last`` through LI's typeahead
  (so mentions render in blue, not as black literal text).
* ``_wait_for_upload_complete`` — block until LI's background media
  upload (video or PDF) signals completion, with an explicit-signal
  fast path and a 60-second fallback.

Both are pure Playwright drivers; nothing in here is route-specific.
Living in ``planning/linkedin`` (instead of ``planning/videos``) reflects
that these are LI-composer concerns, not video-only ones.
"""

from __future__ import annotations

import logging
import re

from playwright.sync_api import Page

logger = logging.getLogger("linkedin_composer")


# ---------- Feed-entry click timeout ----------

# The first feed-action of each Playwright session was timing out on the
# Photo / Video / "Start a post" buttons (issue #27). LinkedIn redirects
# ``/feed/`` to ``/`` for logged-in users and re-renders the share-box client-
# side; the 10 s default we were using wasn't enough for cold sessions, but
# subsequent rows always succeeded within ~1 s because the share box is
# already mounted. A longer click timeout is exactly the right tolerance
# because Playwright's ``.click()`` internally polls for actionability
# (attached + visible + stable + receives events) and returns the instant
# the button is clickable — so the cost on a warm session is essentially
# zero, and only the cold first click pays.
FEED_ENTRY_CLICK_TIMEOUT_MS = 30000


# ---------- Mention resolution ----------

# A LinkedIn mention starts at "@" and runs through one or more capitalized
# tokens separated by single spaces. We intentionally STOP at the first
# non-letter (punctuation, newline) so "@Hannah Wilson. " resolves the mention
# "Hannah Wilson" and leaves the period + space as literal text.
#
# Letters are Unicode-aware (``[^\W\d_]`` = any word char that is not a digit
# or underscore, i.e. any Unicode letter) so accented names — "@Mercè Brey",
# "@Begoña Núñez" — are captured WHOLE. The old ASCII-only ``[a-zA-Z]`` stopped
# at the first accented character, capturing only the prefix ("Merc"), which
# made the chip resolve on the prefix and then dumped the accented tail
# ("è Brey") into the composer as stale literal text beside the blue chip.
#
# stdlib ``re`` has no Unicode uppercase class, so the regex matches a greedy
# run of letter tokens (any case) and ``_leading_capitalized_run`` trims it
# down to the leading run of *capitalized* tokens. That keeps two behaviours
# the old ``[A-Z]``-anchored regex had for free: a lowercase-initial match
# (the "@gmail" of an email) yields an empty run and is skipped, and a name
# does not greedily swallow following lowercase words ("Thanks @John for help"
# resolves "John", not "John for help").
#
# Periods/apostrophes/hyphens inside names ("@O'Connor", "@Jean-Paul") are
# still NOT supported; extend the token class if you hit a real case.
_MENTION_RE = re.compile(r"@([^\W\d_]+(?:\s+[^\W\d_]+)*)", re.UNICODE)
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _leading_capitalized_run(raw: str) -> str:
    """Return the leading run of capitalized whitespace-separated tokens.

    ``raw`` is a ``_MENTION_RE`` group-1 capture (letter tokens joined by
    whitespace). Tokens are kept while their first character is uppercase and
    the result is a verbatim prefix of ``raw`` (original separators preserved),
    so the caller can locate its end with ``match.start(1) + len(result)``.
    Returns ``""`` when the first token is not capitalized.
    """
    end = 0
    for tok in _NAME_TOKEN_RE.finditer(raw):
        if tok.group(0)[0].isupper():
            end = tok.end()
        else:
            break
    return raw[:end]

# Selector candidates for the LinkedIn mention typeahead dropdown, in
# specificity order. LinkedIn's UI varies across rollouts; the generic
# fallbacks at the bottom catch builds that drop the testid / aria hooks.
_MENTION_DROPDOWN_SELECTORS = (
    'div.mentions-typeahead-content [role="option"]',
    'div[data-test-id="mentions-typeahead"] [role="option"]',
    '[aria-label*="mention" i] [role="option"]',
    '.artdeco-typeahead__results-list li',
    '[role="listbox"] [role="option"]',
)


def _click_mention_suggestion(page: Page, name: str, *, timeout_ms: int = 6000) -> bool:
    """Wait for the LI mention typeahead and click the matching option.

    Strategy: poll the candidate selectors above until at least one returns
    a visible option list, then pick the first item whose visible text
    contains the typed name (case-insensitive). If no name match within
    the first six items, click the topmost (LinkedIn's typeahead ranks
    relevant matches first). Returns True on click, False on timeout.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    name_lower = name.lower()
    while page.evaluate("() => Date.now()") < deadline:
        for sel in _MENTION_DROPDOWN_SELECTORS:
            try:
                items = page.locator(sel)
                count = items.count()
                if count == 0:
                    continue
                for i in range(min(count, 6)):
                    item = items.nth(i)
                    try:
                        text = (item.inner_text(timeout=400) or "").lower()
                    except Exception:
                        continue
                    if name_lower in text:
                        try:
                            item.click(timeout=2000)
                            return True
                        except Exception:
                            continue
                try:
                    items.first.click(timeout=2000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        page.wait_for_timeout(200)
    return False


def fill_caption_with_mentions(page: Page, caption: str) -> None:
    """Type ``caption`` into the LI composer, resolving every @mention.

    For each ``@CapitalizedName`` (or ``@First Last``) in the caption:
      1. Type the literal text up to the @.
      2. Type @, then the name letter-by-letter (typeahead populates per
         keystroke; ~80ms delay per char is enough for LinkedIn to fetch).
      3. Click the matching suggestion in the dropdown.
      4. Resume typing the tail.

    If a mention can't be resolved (no dropdown, no matching suggestion),
    the ``@<name>`` stays as literal text and a warning is logged — we
    don't fail the whole row over one missing mention.
    """
    if not caption:
        return
    editor = page.locator('div[role="textbox"][contenteditable="true"]')
    try:
        editor.first.wait_for(state="visible", timeout=10000)
        editor.first.click()
    except Exception as err:
        raise RuntimeError(f"Could not focus the LinkedIn caption editor: {err}")

    pos = 0
    mention_count = 0
    resolved_count = 0
    for m in _MENTION_RE.finditer(caption):
        # Trim the greedy letter run to its leading capitalized tokens. An
        # empty run means the "@" is lowercase-initial (e.g. an email's
        # "@gmail"): leave it untouched — we don't type or advance ``pos``, so
        # it stays in the literal stream typed by the next iteration's leading
        # chunk (or the final tail).
        name = _leading_capitalized_run(m.group(1))
        if not name:
            continue
        name_end = m.start(1) + len(name)  # caption index just past the name
        if m.start() > pos:
            page.keyboard.type(caption[pos:m.start()], delay=4)
        mention_count += 1
        page.keyboard.type("@", delay=20)
        page.wait_for_timeout(250)
        page.keyboard.type(name, delay=80)
        page.wait_for_timeout(400)
        clicked = _click_mention_suggestion(page, name)
        if clicked:
            resolved_count += 1
            logger.info("🔗 Resolved LinkedIn mention @%s", name)
            # LinkedIn quirk: committing the mention chip (clicking the
            # typeahead suggestion) absorbs the immediately-following
            # SPACE keystroke, fusing the next word into the chip
            # ("@Michelle Kempton says" → "Kemptonsays"). Newlines pass
            # through unaffected (verified against the videos flow:
            # "@Hannah Wilson\n\nShe shared" renders correctly with no
            # extra padding). Strategy: peek at the next source char and
            # inject one space when it's whitespace (replace the eaten
            # one) or alphanumeric (defensive — source missed a space).
            # Newlines, punctuation, EOF: leave alone.
            page.wait_for_timeout(150)
            next_char = caption[name_end:name_end+1]
            if next_char in (" ", "\t"):
                page.keyboard.type(" ", delay=40)
                pos = name_end + 1  # skip the (would-be-eaten) source space
                continue
            if next_char and next_char.isalnum():
                page.keyboard.type(" ", delay=40)
        else:
            logger.warning(
                "⚠️ Could not resolve LinkedIn mention @%s — left as literal text. "
                "The composer may still show the unresolved @<name>.", name,
            )
        pos = name_end
    if pos < len(caption):
        page.keyboard.type(caption[pos:], delay=4)
    if mention_count:
        logger.info("📝 Caption typed (%d mentions, %d resolved).",
                    mention_count, resolved_count)
    else:
        logger.debug("📝 Caption typed (%d chars, no mentions).", len(caption))


# ---------- Post-Schedule upload-complete wait ----------

# After the final Schedule click, LinkedIn closes the composer immediately
# but keeps uploading media in the background (videos AND document PDFs).
# Tearing down Playwright before the upload finishes results in a scheduled
# post with no media attached ("Something went wrong, please try reloading").
# We hunt several rollout-dependent signals, then fall back to a fixed
# conservative wait if none is found.
_UPLOAD_IN_PROGRESS_SELECTORS = (
    'div[aria-label*="upload" i]:not([aria-label*="complete" i])',
    'div[role="status"]:has-text("Uploading")',
    'div[role="alert"]:has-text("upload" i)',
    'div:has-text("don\'t close")',
    'div:has-text("Don\'t close")',
    'div:has-text("Do not close")',
    'div:has-text("video is uploading")',
    'div:has-text("Uploading your video")',
    'div:has-text("document is uploading")',
    'div:has-text("Uploading your document")',
    'div.global-alert',
    '[data-test-global-alert-id]',
)

_UPLOAD_COMPLETE_TEXT_RE = re.compile(
    r"(upload\s*complete|video\s*uploaded|document\s*uploaded|"
    r"successfully\s*scheduled|post\s*scheduled|"
    r"your\s*post\s*will\s*be\s*published)",
    re.I,
)


def _upload_in_progress_visible(page: Page) -> bool:
    for sel in _UPLOAD_IN_PROGRESS_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def wait_for_upload_complete(page: Page, *, timeout_ms: int = 420000) -> bool:
    """Block until LI's background media upload signals completion.

    Strategy:
      1. Settle ~2s post-Schedule so the toast/banner has time to mount.
      2. If an explicit "upload complete" / "post scheduled" text appears,
         return immediately.
      3. Otherwise, if an in-progress indicator was at any point visible,
         poll until they all disappear (then add a 3-second buffer).
      4. If no indicator ever showed (LI may already be done, or use a
         selector not on the list), hold the browser open for 60 seconds
         as a safety buffer.

    ``timeout_ms`` is the hard cap on the in-progress polling loop
    (default 7 minutes) — long enough for a 30-50 MB clip on a slow uplink.
    """
    page.wait_for_timeout(2000)

    try:
        if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
            logger.info("✅ LI upload-complete signal already visible — proceeding.")
            page.wait_for_timeout(2000)
            return True
    except Exception:
        pass

    saw_in_progress = _upload_in_progress_visible(page)
    if not saw_in_progress:
        for _ in range(8):  # ~4s
            page.wait_for_timeout(500)
            if _upload_in_progress_visible(page):
                saw_in_progress = True
                break
            try:
                if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
                    logger.info("✅ LI upload-complete signal appeared during settle.")
                    page.wait_for_timeout(2000)
                    return True
            except Exception:
                pass

    if not saw_in_progress:
        logger.info(
            "ℹ️ No LI upload-in-progress indicator detected; holding the browser "
            "open for 60s as a safety buffer so a slow background upload can finish."
        )
        page.wait_for_timeout(60000)
        return True

    logger.info(
        "⏳ LI background upload in progress — polling for completion "
        "(max %ds). Browser will stay open until done.", timeout_ms // 1000,
    )
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last_log = 0
    while page.evaluate("() => Date.now()") < deadline:
        try:
            if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
                logger.info("✅ LI upload-complete text detected.")
                page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
        if not _upload_in_progress_visible(page):
            logger.info("✅ LI upload-in-progress indicator gone; upload likely complete.")
            page.wait_for_timeout(3000)
            return True
        now = page.evaluate("() => Date.now()")
        if now - last_log > 15000:
            logger.info("⏳ ...still uploading (%ds elapsed).",
                        (now - (deadline - timeout_ms)) // 1000)
            last_log = now
        page.wait_for_timeout(2000)

    logger.warning(
        "⚠️ LI upload-complete signal not received within %ds. "
        "The scheduled post MAY be incomplete — verify in Scheduled posts.",
        timeout_ms // 1000,
    )
    return False


__all__ = [
    "fill_caption_with_mentions",
    "wait_for_upload_complete",
]
