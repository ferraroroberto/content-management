"""Capture engine + README generator for the control-panel screenshots.

Design (issue #110):

* **Manifest is the contract** — ``docs/screenshots/manifest.json`` declares
  every capturable feature. The engine never invents targets.
* **Fail-safe masking** — an entry without ``mask`` selectors is skipped with
  a loud warning and never captured raw. All tabs render live social metrics
  and subscriber data; "no sensitive data on any screenshot, ever" is
  enforced here, not hoped for.
* **Idempotency by INPUT hash** — the sha256 of the files matched by
  ``source_globs`` plus the entry's capture config decides whether a shot is
  stale. PNG output bytes are never hashed (they drift run-to-run).
* **Stable filenames** — ``docs/screenshots/<feature>-desktop.png``; the
  timestamp lives in manifest metadata only, so git diffs stay clean.
* Browser launch options come from ``config.chrome_launch`` (the
  ``doc_capture_*`` variant) — never re-inlined here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "screenshots" / "manifest.json"
README_PATH = REPO_ROOT / "README.md"

MARKER_START = "<!-- docs-shots:start -->"
MARKER_END = "<!-- docs-shots:end -->"

# Neutral gray for masked regions — less jarring in docs than Playwright's
# default pink, and unmistakably "redacted" rather than "broken render".
MASK_COLOR = "#B3B3B3"

DEFAULT_BASE_URL = "http://localhost:8501"

ACTION_CAPTURE = "capture"
ACTION_SKIP_UNMASKED = "skip-unmasked"
ACTION_SKIP_UNCHANGED = "skip-unchanged"

# Only these keys feed the input hash: they change what the capture looks
# like. description/title changes only affect the README block.
_CAPTURE_CONFIG_KEYS = ("reach", "mask", "wait")


@dataclass
class PlanItem:
    """One feature's fate for this run, decided before any browser starts."""

    name: str
    action: str
    reason: str
    new_hash: Optional[str] = None


# ── manifest ────────────────────────────────────────────────────────


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_manifest(manifest: dict[str, Any], path: Path = MANIFEST_PATH) -> None:
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


# ── idempotency ─────────────────────────────────────────────────────


def compute_input_hash(entry: dict[str, Any], repo_root: Path = REPO_ROOT) -> str:
    """sha256 over the entry's capture config + every file its source_globs
    match (posix relpath + bytes, sorted). Never hashes PNG output."""
    h = hashlib.sha256()
    capture_config = {k: entry.get(k) for k in _CAPTURE_CONFIG_KEYS}
    h.update(json.dumps(capture_config, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    matched: set[Path] = set()
    for pattern in entry.get("source_globs", []):
        matched.update(p for p in repo_root.glob(pattern) if p.is_file())
    for f in sorted(matched):
        h.update(f.relative_to(repo_root).as_posix().encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def screenshot_path(name: str, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "docs" / "screenshots" / f"{name}-desktop.png"


# ── planning (pure — unit-tested without a browser) ─────────────────


def plan_features(
    manifest: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    force: bool = False,
    only: Optional[list[str]] = None,
) -> list[PlanItem]:
    """Decide per feature: capture, skip-unchanged, or refuse-unmasked.

    The fail-safe lives here: no ``mask`` (missing or empty) → the feature is
    never captured, loudly. ``capture_features`` only acts on ACTION_CAPTURE
    items, so nothing downstream can bypass this gate.
    """
    items: list[PlanItem] = []
    for name, entry in manifest["features"].items():
        if only and name not in only:
            continue
        if not entry.get("mask"):
            logger.warning(
                "⚠️ %s: no 'mask' selectors in the manifest — REFUSING to capture raw "
                "(fail-safe; every tab renders live data). Add mask selectors to capture it.",
                name,
            )
            items.append(PlanItem(name, ACTION_SKIP_UNMASKED, "no mask selectors configured"))
            continue
        new_hash = compute_input_hash(entry, repo_root)
        if (
            not force
            and entry.get("input_hash") == new_hash
            and screenshot_path(name, repo_root).exists()
        ):
            items.append(PlanItem(name, ACTION_SKIP_UNCHANGED, "input hash unchanged", new_hash))
            continue
        items.append(
            PlanItem(name, ACTION_CAPTURE, "forced" if force else "input changed or never captured", new_hash)
        )
    return items


# ── capture ─────────────────────────────────────────────────────────


def _preflight(base_url: str) -> None:
    import requests

    try:
        requests.get(base_url, timeout=5)
    except requests.RequestException as exc:
        raise SystemExit(
            f"❌ control-panel app unreachable at {base_url} — start it first "
            f"(launch_app.bat) or pass --base-url. ({exc})"
        ) from exc


def _wait_settled(page: Any) -> None:
    """Wait for Streamlit to finish its rerun: the status widget ("Running…")
    goes away, the wire goes quiet, plus a fixed settle beat."""
    try:
        page.locator('[data-testid="stStatusWidget"]').first.wait_for(state="hidden", timeout=15_000)
    except Exception:  # noqa: BLE001 — widget may never have appeared; that's settled too
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 — an autorefreshing log panel keeps the wire warm
        pass
    page.wait_for_timeout(1_200)


# Streamlit 1.57 renders st.segmented_control as a stButtonGroup of
# stBaseButton-segmented_control[Active] buttons — there is no
# "stSegmentedControl" testid. Waiting for the button group doubles as the
# "websocket render finished" signal for the whole app shell.
_SECTION_ROUTER = '[data-testid="stButtonGroup"]'
_SECTION_BUTTON = 'button[data-testid^="stBaseButton-segmented_control"]'


def _collapse_sidebar(page: Any) -> bool:
    """Collapse the sidebar (live clock + pipeline status — all volatile).
    Returns False when the control isn't found; the caller then masks the
    whole sidebar instead — fail-safe either way."""
    try:
        sidebar = page.locator('section[data-testid="stSidebar"]')
        # Already collapsed (Streamlit remembers the state per browser session,
        # so pages after the first start collapsed) — nothing to do.
        if sidebar.count() == 0 or sidebar.first.get_attribute("aria-expanded") == "false":
            return True
        btn = page.locator('[data-testid="stSidebarCollapseButton"] button')
        if btn.count() == 0:
            return False
        # The control is hover-revealed; a forced click on the hidden button
        # dispatches but doesn't collapse. Hover first, click for real.
        page.locator('[data-testid="stSidebarHeader"]').hover()
        btn.first.click()
        page.wait_for_timeout(600)
        # Trust the DOM, not the click: only aria-expanded=false counts.
        sidebar = page.locator('section[data-testid="stSidebar"]')
        return sidebar.count() == 0 or sidebar.first.get_attribute("aria-expanded") == "false"
    except Exception:  # noqa: BLE001 — any uncertainty falls back to masking
        return False


def _reach_feature(page: Any, entry: dict[str, Any]) -> None:
    reach = entry.get("reach") or {}
    label = reach.get("label")
    # click: false → the app's default section; clicking the already-selected
    # segmented-control option would deselect it (blank pill in the shot).
    if label and reach.get("click", True):
        # .first = the top-level section router in app.py (a tab may render
        # its own nested segmented control below it, e.g. engagement).
        page.locator(_SECTION_ROUTER).first.locator(_SECTION_BUTTON).filter(
            has_text=label
        ).click()
    wait = entry.get("wait") or {}
    if wait.get("selector"):
        page.wait_for_selector(wait["selector"], timeout=30_000)
    if wait.get("text"):
        page.get_by_text(wait["text"]).first.wait_for(timeout=30_000)
    # Open any expanders named in reach.expand: content hidden in a collapsed
    # expander still occupies DOM boxes, so its masks would paint gray blocks
    # over unrelated visible UI. Expanding renders (and masks) it in place.
    for exp_label in reach.get("expand", []):
        page.locator('[data-testid="stExpander"]').filter(has_text=exp_label).first.locator(
            "summary"
        ).click()
        page.wait_for_timeout(800)
    _wait_settled(page)


def capture_features(
    manifest: dict[str, Any],
    items: list[PlanItem],
    *,
    base_url: str = DEFAULT_BASE_URL,
    headless: bool = True,
    repo_root: Path = REPO_ROOT,
) -> int:
    """Capture every ACTION_CAPTURE item; update the entries' engine-maintained
    metadata in place. Returns the number of screenshots taken."""
    todo = [i for i in items if i.action == ACTION_CAPTURE]
    for item in items:
        if item.action != ACTION_CAPTURE:
            logger.info("⏭️ %s: %s (%s)", item.name, item.action, item.reason)
    if not todo:
        logger.info("✅ nothing to capture")
        return 0

    _preflight(base_url)

    from playwright.sync_api import sync_playwright

    from config.chrome_launch import (
        DOC_CAPTURE_SETTLE_CSS,
        doc_capture_context_kwargs,
        doc_capture_launch_kwargs,
    )

    captured = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**doc_capture_launch_kwargs(headless=headless))
        context = browser.new_context(**doc_capture_context_kwargs())
        try:
            for item in todo:
                entry = manifest["features"][item.name]
                page = context.new_page()
                try:
                    page.goto(base_url, wait_until="load", timeout=60_000)
                    page.wait_for_selector(_SECTION_ROUTER, timeout=30_000)
                    page.add_style_tag(content=DOC_CAPTURE_SETTLE_CSS)
                    masks = list(entry["mask"])
                    if not _collapse_sidebar(page):
                        masks.append('section[data-testid="stSidebar"]')
                        logger.warning(
                            "⚠️ %s: sidebar collapse control not found — masking the whole sidebar",
                            item.name,
                        )
                    _reach_feature(page, entry)
                    out = screenshot_path(item.name, repo_root)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(out),
                        mask=[page.locator(sel) for sel in masks],
                        mask_color=MASK_COLOR,
                    )
                    entry["input_hash"] = item.new_hash
                    entry["captured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    entry["files"] = [out.relative_to(repo_root).as_posix()]
                    captured += 1
                    logger.info(
                        "✅ %s → %s (%d mask selectors)",
                        item.name,
                        out.relative_to(repo_root).as_posix(),
                        len(masks),
                    )
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()
    return captured


# ── README generator ────────────────────────────────────────────────


def render_readme_block(manifest: dict[str, Any]) -> str:
    """Deterministic feature block from the manifest. Entries never captured
    (no ``files``) are omitted — no broken image links in the README."""
    lines: list[str] = []
    for name, entry in manifest["features"].items():
        files = entry.get("files") or []
        if not files:
            logger.warning("⚠️ %s: never captured — omitted from the README block", name)
            continue
        title = entry.get("title", name)
        lines.append(f"### {title}")
        lines.append("")
        lines.append(entry["description"])
        lines.append("")
        for f in files:
            lines.append(f"![{title} screenshot]({f})")
        lines.append("")
    return "\n".join(lines).rstrip()


def regenerate_readme(
    manifest: dict[str, Any], readme_path: Path = README_PATH
) -> bool:
    """Rewrite the block between the regen markers in place. Returns True when
    the README changed; rerun with an unchanged manifest is a no-op."""
    text = readme_path.read_text(encoding="utf-8")
    if MARKER_START not in text or MARKER_END not in text:
        raise SystemExit(
            f"❌ README regen markers missing — add '{MARKER_START}' and "
            f"'{MARKER_END}' once where the screenshots section belongs."
        )
    pre, rest = text.split(MARKER_START, 1)
    _, post = rest.split(MARKER_END, 1)
    block = render_readme_block(manifest)
    new = f"{pre}{MARKER_START}\n\n{block}\n\n{MARKER_END}{post}"
    if new == text:
        logger.info("✅ README unchanged")
        return False
    readme_path.write_text(new, encoding="utf-8", newline="\n")
    logger.info("✅ README screenshots section regenerated")
    return True
