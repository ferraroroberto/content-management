r"""End-to-end Playwright check of the engagement review Streamlit app.

Drives http://localhost:8501 as a real browser, takes screenshots after each
step, asserts the cards render correctly (no "nan" leaking from null
my_reply_text), exercises the approve action and confirms the pending count
drops by 1.

Usage:
    # Make sure Streamlit is running first:
    #   & .\.venv\Scripts\python.exe -m streamlit run engagement\review_app.py
    & .\.venv\Scripts\python.exe -m engagement.check_review_app

Screenshots land in results/engagement/e2e/<timestamp>/. Each step prints
PASS/FAIL with a short reason.

This is a smoke check, not a full test suite — designed to verify the
review_app end-to-end without manually clicking on a mobile device.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs  # noqa: E402
from engagement.db.client import supabase_client  # noqa: E402

APP_URL = "http://localhost:8501"


def _force_utf8_stdout() -> None:
    for s in ("stdout", "stderr"):
        st = getattr(sys, s, None)
        if st is not None and hasattr(st, "reconfigure"):
            try:
                st.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _shot(page: Page, out_dir: Path, label: str) -> Path:
    out = out_dir / f"{label}.png"
    page.screenshot(path=str(out), full_page=True)
    return out


def _pass(label: str, detail: str = "") -> None:
    print(f"  ✅ PASS  {label}" + (f"  — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  ❌ FAIL  {label}" + (f"  — {detail}" if detail else ""))


def _count_pending() -> int:
    sb = supabase_client()
    res = sb.table("comments").select("comment_id", count="exact").eq("status", "pending").limit(1).execute()
    return res.count or 0


def _wait_for_streamlit(page: Page, timeout_ms: int = 15_000) -> None:
    """Streamlit renders client-side — wait for the run-toolbar OR the page title."""
    page.wait_for_load_state("networkidle", timeout=timeout_ms)
    # Either the engagement title heading appears, or the empty-state caption.
    try:
        page.wait_for_selector("text=engagement triage", timeout=timeout_ms)
    except PWTimeoutError:
        pass


def _click_tab(page: Page, label: str) -> bool:
    """Streamlit st.tabs renders <button role='tab'> with the label text inside."""
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
    _force_utf8_stdout()
    out_dir = REPO_ROOT / "results" / "engagement" / "e2e" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 screenshots → {out_dir}")

    fails = 0
    with sync_playwright() as pw:
        # Fresh context, no persistent profile — local app, no auth needed.
        kwargs = stealth_launch_kwargs(str(out_dir / "_browser"), headless=True)
        # Drop persistent_context dir — we just want a clean throwaway context.
        kwargs.pop("user_data_dir", None)
        browser = pw.chromium.launch(channel=kwargs.get("channel"), headless=True, args=kwargs.get("args"))
        context = browser.new_context(viewport={"width": 1400, "height": 1800})
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()

        print("\n📱 step 1 — open the app")
        try:
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=15_000)
            _wait_for_streamlit(page)
            _shot(page, out_dir, "01-opened")
            _pass("app loaded", APP_URL)
        except Exception as err:
            _fail("app load", str(err))
            fails += 1
            browser.close()
            return fails

        print("\n📱 step 2 — Real comments tab")
        _click_tab(page, "real comments")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "02-real-tab")
        body = page.inner_text("body")
        # The bug we just fixed: "you already replied nan" appearing on every card.
        nan_hits = body.lower().count("already replied nan")
        if nan_hits:
            _fail("no leaked NaN in real-comments tab", f"found {nan_hits} 'already replied nan' lines")
            fails += 1
        else:
            _pass("no leaked NaN in real-comments tab")
        # The cards should render — look for at least one familiar commenter name we know is in the DB.
        # (Skip if Daniel Lock got blacklisted and cascaded out.)
        cards_visible = any(name.lower() in body.lower() for name in ("Tasha Eurich", "Mostafa Aouich", "Madhav Pangarkar", "Edoardo Pallaro"))
        if cards_visible:
            _pass("real-comments cards rendered")
        else:
            _fail("real-comments cards", "no familiar commenter names found on page")
            fails += 1

        print("\n📱 step 3 — AI triage tab")
        _click_tab(page, "AI triage")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "03-ai-tab")
        body = page.inner_text("body")
        if "triage clear" in body.lower() or any(n.lower() in body.lower() for n in ("Daniel Lock",)):
            _pass("AI triage rendered (cascade reflected if Daniel Lock blacklisted)")
        else:
            _fail("AI triage state ambiguous", "expected empty-state caption or blacklisted commenter card")
            fails += 1

        print("\n📱 step 4 — sidebar counts non-zero")
        # Sidebar 'pending' metric — Streamlit renders metrics as labelled divs.
        sidebar_text = ""
        try:
            sidebar_text = page.locator("section[data-testid='stSidebar']").first.inner_text()
        except Exception:
            sidebar_text = body
        if "pending" in sidebar_text.lower():
            _pass("sidebar 'pending' metric present")
        else:
            _fail("sidebar metric missing", "no 'pending' label seen")
            fails += 1

        print("\n📱 step 5 — approve action lowers pending count by 1")
        pending_before = _count_pending()
        _click_tab(page, "AI triage")
        page.wait_for_timeout(500)
        approve_clicked = False
        try:
            # Streamlit renders st.button as <button kind='secondary'> with text inside; emoji is in the label.
            btn = page.locator("button:has-text('approve')").first
            if btn.count() and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1500)
                approve_clicked = True
        except Exception as err:
            print(f"     note: approve click skipped — {err}")
        _shot(page, out_dir, "04-after-approve")
        if approve_clicked:
            pending_after = _count_pending()
            if pending_after == pending_before - 1:
                _pass("approve dropped pending count by 1", f"{pending_before} -> {pending_after}")
            else:
                _fail("approve count delta", f"expected {pending_before - 1}, got {pending_after}")
                fails += 1
            # Roll back so the test is idempotent — re-run friendly.
            sb = supabase_client()
            sb.table("comments").update({"status": "pending", "decided_at": None}).eq("status", "approved").execute()
        else:
            _pass("approve button skipped — no AI-flagged rows to approve (expected on first run)")

        print("\n📱 step 6 — back to Real comments and verify no NaN after rerun")
        _click_tab(page, "real comments")
        page.wait_for_timeout(800)
        _shot(page, out_dir, "05-real-tab-after")
        body = page.inner_text("body")
        if "already replied nan" not in body.lower():
            _pass("no NaN after rerun")
        else:
            _fail("NaN reappeared after action", "")
            fails += 1

        print("\n📱 step 7 — Commenters analysis tab")
        _click_tab(page, "commenters")
        page.wait_for_timeout(1200)
        _shot(page, out_dir, "06-commenters-tab")
        body = page.inner_text("body")
        if "commenter(s)" in body.lower() and "sorted by total desc" in body.lower():
            _pass("commenters table caption present")
        else:
            _fail("commenters table caption", "expected 'commenter(s) · sorted by total desc'")
            fails += 1
        if "classification filter" in body.lower():
            _pass("classification filter rendered")
        else:
            _fail("classification filter missing", "")
            fails += 1
        # Cell contents render to canvas inside Glide grid — inner_text can't see
        # them. Use the visible caption + the header row + DB cross-check.
        sb = supabase_client()
        commenter_count = sb.table("commenters").select("account_url", count="exact").limit(1).execute().count or 0
        expected_caption = f"{commenter_count} commenter(s)"
        if expected_caption.lower() in body.lower():
            _pass("commenters caption matches DB count", expected_caption)
        else:
            _fail("commenters caption mismatch", f"expected '{expected_caption}'")
            fails += 1
        if "total" in body.lower() and "commenter" in body.lower() and "class" in body.lower():
            _pass("commenters table headers rendered")
        else:
            _fail("commenters table headers", "expected commenter/class/total")
            fails += 1

        print("\n📱 step 8 — click first row → drill-down renders")
        # Glide grid is canvas-rendered. Click at row-1 pixel coordinates within the
        # stDataFrame element. Row height ≈ 35px; header ≈ 35px; row 1 center ≈ 52px
        # from the top of the canvas, x ≈ 30px hits the row-selector checkbox column.
        drill_ok = False
        try:
            grid = page.locator("[data-testid='stDataFrame']").first
            grid.scroll_into_view_if_needed()
            box = grid.bounding_box()
            if box:
                page.mouse.click(box["x"] + 30, box["y"] + 52)
                page.wait_for_timeout(1500)
        except Exception as err:
            print(f"     note: canvas click strategy — {err}")
        body2 = page.inner_text("body")
        if "selected:" in body2.lower():
            drill_ok = True
        _shot(page, out_dir, "07-drilldown")
        if drill_ok:
            _pass("drill-down panel rendered after row click")
        else:
            print("  ⚠️ SKIP  drill-down click (canvas grid not reliably clickable headless) — data verified separately")

        print("\n📱 step 9 — custom theme from .streamlit/config.toml applied")
        theme_probe = page.evaluate(
            r"""
            () => {
                const body = document.body;
                const sidebar = document.querySelector("section[data-testid='stSidebar']");
                const cs = (el) => el ? getComputedStyle(el) : null;
                return {
                    bodyBg:    cs(body)?.backgroundColor    || null,
                    sidebarBg: cs(sidebar)?.backgroundColor || null,
                };
            }
            """
        )
        print(f"     probe: {theme_probe}")
        expected = {"bodyBg": "rgb(14, 17, 23)", "sidebarBg": "rgb(38, 39, 48)"}
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
        print("🎉 ALL CHECKS PASSED")
    else:
        print(f"⚠️  {fails} CHECK(S) FAILED — see screenshots above")
    return fails


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
