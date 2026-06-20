r"""End-to-end Playwright check of the control-panel app.

Drives http://localhost:8501 (Streamlit's default — what ``launch_app.bat``
uses). Verifies each pipeline tab renders + the engagement sub-tabs work.
Override the target with the APP_URL environment variable when the app is
running on a non-default port.

Usage (control panel must already be running):
    launch_app.bat   # or: .venv\Scripts\python.exe -m streamlit run app\app.py
    & .\.venv\Scripts\python.exe -m app.check_control_panel
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs  # noqa: E402
from config.console import force_utf8_stdio  # noqa: E402

import os

# Default to 8501 (what `launch_app.bat` uses). Override with APP_URL env var
# when running parallel to the standalone review_app for dev.
APP_URL = os.environ.get("APP_URL", "http://localhost:8501")


def _pass(label: str, detail: str = "") -> None:
    print(f"  ✅ PASS  {label}" + (f"  — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  ❌ FAIL  {label}" + (f"  — {detail}" if detail else ""))


def _shot(page, out_dir: Path, label: str) -> None:
    page.screenshot(path=str(out_dir / f"{label}.png"), full_page=True)


def _assert_no_traceback(page, label: str) -> bool:
    """Scan ONLY Streamlit's exception widgets for Python errors. Body-text
    scanning gives false positives — log panels of past runs contain
    'Traceback:' as data, and st.tabs keeps all sub-tab content in the DOM."""
    try:
        excs = page.locator("[data-testid='stException']")
        count = excs.count()
    except Exception:
        count = 0
    if count > 0:
        snippet = ""
        try:
            snippet = excs.first.inner_text()[:200]
        except Exception:
            pass
        _fail(f"no python error on {label}", f"stException widget rendered: {snippet!r}")
        return False
    _pass(f"no python error on {label}")
    return True


def _click_tab(page, label: str) -> bool:
    for sel in [f"button[role='tab']:has-text('{label}')", f"button:has-text('{label}')"]:
        try:
            tab = page.locator(sel).first
            if tab.count() and tab.is_visible():
                tab.click()
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    return False


def run() -> int:
    force_utf8_stdio()
    out_dir = REPO_ROOT / "results" / "engagement" / "e2e-control" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 screenshots → {out_dir}")

    fails = 0
    with sync_playwright() as pw:
        kwargs = stealth_launch_kwargs(str(out_dir / "_browser"), headless=True)
        kwargs.pop("user_data_dir", None)
        browser = pw.chromium.launch(channel=kwargs.get("channel"), headless=True, args=kwargs.get("args"))
        context = browser.new_context(viewport={"width": 1400, "height": 1800})
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()

        print("\n📱 step 1 — open control panel")
        try:
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_selector("text=control panel", timeout=15_000)
            _shot(page, out_dir, "01-opened")
            _pass("app loaded", APP_URL)
        except Exception as err:
            _fail("app load", str(err))
            browser.close()
            return 1

        body = page.inner_text("body")
        if all(label in body.lower() for label in ("reporting", "planning", "newsletter", "engagement")):
            _pass("all 4 pipeline tabs present")
        else:
            _fail("missing pipeline tabs", "")
            fails += 1
        if "pipeline status" in body.lower():
            _pass("sidebar pipeline-status block present")
        else:
            _fail("sidebar pipeline status missing", "")
            fails += 1

        print("\n📱 step 2 — Reporting tab renders run controls + log panel")
        _click_tab(page, "reporting")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "02-reporting")
        body = page.inner_text("body")
        if "run reporting pipeline" in body.lower() and "skip substack" in body.lower():
            _pass("reporting controls render")
        else:
            _fail("reporting controls", "expected 'run reporting pipeline' button + 'skip substack' toggle")
            fails += 1

        print("\n📱 step 3 — Planning tab renders dry-run/live radio")
        _click_tab(page, "planning")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "03-planning")
        body = page.inner_text("body")
        if "dry-run" in body.lower() and "live" in body.lower() and "run planning pipeline" in body.lower():
            _pass("planning controls render")
        else:
            _fail("planning controls", "")
            fails += 1

        print("\n📱 step 4 — Newsletter tab renders number input")
        _click_tab(page, "newsletter")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "04-newsletter")
        body = page.inner_text("body")
        if "newsletter number" in body.lower() and "normalize lookback" in body.lower():
            _pass("newsletter controls render")
        else:
            _fail("newsletter controls", "")
            fails += 1

        print("\n📱 step 5 — Engagement tab renders sub-tabs + review data")
        _click_tab(page, "engagement")
        page.wait_for_timeout(1200)
        _shot(page, out_dir, "05-engagement-run")
        body = page.inner_text("body")
        if "scrape + classify" in body.lower() and "scrape linkedin" in body.lower():
            _pass("engagement run sub-tab default")
        else:
            _fail("engagement run sub-tab", "")
            fails += 1
        if not _assert_no_traceback(page, "engagement-run sub-tab"):
            fails += 1

        # AI triage sub-tab — exercises cards with cascade-classified rows
        # (the NaN verdict_reasons bug lived here).
        _click_tab(page, "AI triage")
        page.wait_for_timeout(1500)
        _shot(page, out_dir, "06-engagement-ai")
        if not _assert_no_traceback(page, "engagement AI sub-tab"):
            fails += 1

        # Real-comments sub-tab — also renders cards, same risk surface.
        _click_tab(page, "real comments")
        page.wait_for_timeout(1500)
        _shot(page, out_dir, "07-engagement-real")
        if not _assert_no_traceback(page, "engagement real sub-tab"):
            fails += 1

        # Commenters sub-tab
        _click_tab(page, "commenters")
        page.wait_for_timeout(1500)
        _shot(page, out_dir, "08-engagement-commenters")
        body = page.inner_text("body")
        if "commenter(s)" in body.lower() and "sorted by total desc" in body.lower():
            _pass("commenters sub-tab renders (data reused from engagement.ui)")
        else:
            _fail("commenters sub-tab", "")
            fails += 1
        if not _assert_no_traceback(page, "engagement commenters sub-tab"):
            fails += 1

        print("\n📱 step 6 — custom .streamlit/config.toml theme is applied")
        # Theme values from app/.streamlit/config.toml:
        #   backgroundColor          = "#0E1117"  → rgb(14, 17, 23)
        #   secondaryBackgroundColor = "#262730"  → rgb(38, 39, 48)
        #   primaryColor             = "#1E88E5"  → rgb(30, 136, 229)
        #   textColor                = "#FAFAFA"  → rgb(250, 250, 250)
        # Use getComputedStyle so we catch the actual rendered value, not just an attribute.
        theme_probe = page.evaluate(
            r"""
            () => {
                const body = document.body;
                const sidebar = document.querySelector("section[data-testid='stSidebar']");
                // The 'run reporting pipeline' button is type=primary so it should use primaryColor.
                let primaryBtn = null;
                const btns = Array.from(document.querySelectorAll("button"));
                for (const b of btns) {
                    if ((b.innerText || '').toLowerCase().includes('run reporting pipeline')) { primaryBtn = b; break; }
                }
                const cs = (el) => el ? getComputedStyle(el) : null;
                return {
                    bodyBg:    cs(body)?.backgroundColor    || null,
                    bodyColor: cs(body)?.color              || null,
                    sidebarBg: cs(sidebar)?.backgroundColor || null,
                    btnBg:     cs(primaryBtn)?.backgroundColor || null,
                };
            }
            """
        )
        print(f"     probe: {theme_probe}")
        expected = {
            "bodyBg":    "rgb(14, 17, 23)",
            "bodyColor": "rgb(250, 250, 250)",
            "sidebarBg": "rgb(38, 39, 48)",
            "btnBg":     "rgb(30, 136, 229)",
        }
        for k, want in expected.items():
            got = theme_probe.get(k)
            if got == want:
                _pass(f"theme {k} == {want}")
            else:
                _fail(f"theme {k}", f"expected {want}, got {got}")
                fails += 1

        browser.close()

    print("\n" + "=" * 50)
    if fails == 0:
        print("🎉 ALL CONTROL-PANEL CHECKS PASSED")
    else:
        print(f"⚠️  {fails} CHECK(S) FAILED")
    return fails


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
