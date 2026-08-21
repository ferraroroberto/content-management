"""Hand-off after a review — the two writes the triage never does on its own (issue #212).

    python -m newsletter.triage.handoff --run 6 --open              # ticked URLs → tabs in the :9222 Chrome
    python -m newsletter.triage.handoff --run 6 --mark-reviewed     # "until … > included" comment on the Notion task page
    python -m newsletter.triage.handoff --run 6                     # neither: print what both would do

``--open``: ``bootstrap_chrome.ensure_chrome()`` (targeted, idempotent) then one new tab per ticked URL
of the run's window (from ``triage_decisions``; falls back to the engine's suggested picks only when
the owner has not applied a review, and says so). Idempotent against tabs already open. The unchanged
``newsletter_pipeline.py archive`` step then takes over.

``--mark-reviewed``: exactly one comment on the configured task page (``config.newsletter_triage.
notion_task_page_id``) in the owner's own style — ``until Fri 07/08 3:17 PM > included`` — where the
timestamp is the newest e-mail of the window in local time, then ``state.json → reviewed_until``.
Without the flag the line is only printed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_block, load_full_config  # noqa: E402
from newsletter.cache import TRACKING_PARAM_EXACT, TRACKING_PARAM_PREFIXES, canonicalize_url  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage.state import load_state, save_state  # noqa: E402

logger = logging.getLogger("newsletter_triage.handoff")


# ---------------------------------------------------------------------------
# what to open


def ticked_urls(run_id: int) -> Tuple[List[str], str]:
    """URLs to hand off for a stored run: the owner's ticks (``pick=True``) for the run's window, else
    the engine's suggested picks. Returns ``(urls, source)`` with source ``decisions`` | ``suggestions``."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")
    decisions = db.load_decisions(run["window_start"], run["window_end"])
    if decisions:
        urls = [open_url(d.get("url"), d.get("canonical")) for d in decisions if d.get("pick")]
        return _dedupe([u for u in urls if u]), "decisions"
    cands = db.candidates(run_id)
    urls = [open_url(c.get("url"), c.get("canonical"))
            for c in sorted(cands, key=lambda c: (c.get("topic") or "", c.get("suggested_rank") or 99))
            if c.get("suggested") == "pick"]
    return _dedupe([u for u in urls if u]), "suggestions"


_APP_LINK_HOSTS = ("open.substack.com", "substack.com")


def open_url(url: Optional[str], canonical: Optional[str]) -> str:
    """The URL to put in a tab: the original href with tracking params stripped (host kept as-is — the
    canonical form drops ``www.`` and reorders the query, fine for a dedupe key, not always loadable);
    Substack app-links / redirects use the canonical post URL instead."""
    if not url:
        return canonical or ""
    parts = urlsplit(url.strip())
    if canonical and parts.netloc.lower() in _APP_LINK_HOSTS:
        return canonical
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(TRACKING_PARAM_PREFIXES) and k.lower() not in TRACKING_PARAM_EXACT]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _dedupe(urls: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for u in urls:
        k = canonicalize_url(u)
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


def plan_open(urls: Sequence[str], already_open: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Split ``urls`` into (to_open, skipped) against the tabs already open — canonical match."""
    open_keys = {canonicalize_url(u) for u in already_open if u}
    to_open, skipped = [], []
    for u in urls:
        (skipped if canonicalize_url(u) in open_keys else to_open).append(u)
    return to_open, skipped


def open_in_chrome(urls: Sequence[str], *, debug_port: int = 9222, dry_run: bool = False) -> Dict[str, Any]:
    """Ensure the newsletter Chrome, open one tab per URL not already open. Returns counts + the skipped list.
    ``dry_run`` does everything except opening: ensure Chrome, connect, list tabs, print the plan."""
    from newsletter import chrome_tabs  # noqa: PLC0415 — Playwright, imported only when opening
    from newsletter.bootstrap_chrome import ensure_chrome  # noqa: PLC0415

    rc = ensure_chrome()
    if rc != 0:
        raise RuntimeError(f"Chrome on :{debug_port} not reachable (bootstrap exit {rc})")
    browser = chrome_tabs.connect(debug_port)
    try:
        open_urls = [t.url for t in chrome_tabs.list_tabs(browser)]
        to_open, skipped = plan_open(urls, open_urls)
        logger.info("🔎 Chrome :%d has %d tab(s) open · %d to open · %d already open", debug_port, len(open_urls),
                    len(to_open), len(skipped))
        if dry_run:
            for u in to_open:
                logger.info("   would open · %s", u[:110])
            for u in skipped:
                logger.info("   already open · %s", u[:110])
            return {"opened": 0, "would_open": len(to_open), "skipped_open": len(skipped), "skipped": skipped,
                    "total": len(urls), "dry_run": True}
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        opened = 0
        for u in to_open:
            page = context.new_page()
            try:
                # "commit" = the navigation started; the page keeps loading in its tab while we move on
                # (domcontentloaded waited up to 30 s on ad-heavy sites — 24 tabs took 12 min).
                page.goto(u, wait_until="commit", timeout=15_000)
            except Exception as err:  # noqa: BLE001 — a slow page is still an open tab; archive handles it
                logger.warning("⚠️ %s — %s (tab left open)", u[:80], str(err)[:80])
            opened += 1
            logger.info("🌐 opened %d/%d · %s", opened, len(to_open), u[:100])
    finally:
        chrome_tabs.close_browser(browser)
    return {"opened": opened, "skipped_open": len(skipped), "skipped": skipped, "total": len(urls)}


# ---------------------------------------------------------------------------
# watermark


def window_until(run_id: int) -> Optional[datetime]:
    """Newest e-mail timestamp of the run (UTC-aware), or None when the run has no emails."""
    emails = db.emails(run_id)
    stamps = []
    for e in emails:
        ts = e.get("ts")
        if not ts:
            continue
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        stamps.append(d if d.tzinfo else d.replace(tzinfo=timezone.utc))
    return max(stamps) if stamps else None


def watermark_line(until: datetime, *, window: Optional[Tuple[str, str]] = None) -> str:
    """``until Fri 07/08 3:17 PM > included`` in local time — the owner's own Gmail-style comment."""
    local = until.astimezone()
    hour = local.strftime("%I").lstrip("0") or "12"
    stamp = f"{local.strftime('%a')} {local.strftime('%d/%m')} {hour}:{local.strftime('%M %p')}"
    line = f"until {stamp} > included"
    if window:
        line += f" (triage {window[0]} → {window[1]})"
    return line


def mark_reviewed(run_id: int, *, line: str, page_id: Optional[str] = None, client: Any = None) -> Dict[str, Any]:
    """Create exactly one comment on the task page and advance the local watermark. Returns the comment id."""
    page_id = page_id or load_block("newsletter_triage").get("notion_task_page_id")
    if not page_id:
        raise RuntimeError("config.newsletter_triage.notion_task_page_id is not set")
    if client is None:
        from newsletter import notion_io  # noqa: PLC0415
        client = notion_io.init_client(load_full_config()["notion"]["api_token"])
    res = client.comments.create(parent={"page_id": page_id}, rich_text=[{"type": "text", "text": {"content": line}}])
    run = db.get_run(run_id) or {}
    state = load_state()
    end = str(run.get("window_end") or "")[:10]
    if end and end > (state.get("reviewed_until") or "")[:10]:
        state["reviewed_until"] = end
    state.setdefault("watermarks", []).append({"run_id": run_id, "line": line, "comment_id": res.get("id"),
                                               "at": datetime.now(timezone.utc).isoformat()})
    save_state(state)
    logger.info("📝 Notion comment created (%s): %s", res.get("id"), line)
    return {"comment_id": res.get("id"), "line": line, "reviewed_until": state.get("reviewed_until")}


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, required=True, help="stored run id (control panel → stored run)")
    ap.add_argument("--open", action="store_true", help="open the ticked URLs as tabs in the :9222 Chrome")
    ap.add_argument("--mark-reviewed", action="store_true", help="write the watermark comment on the Notion task page")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --open: ensure Chrome, connect, list tabs and print the plan — open nothing")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "hpack", "notion_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        db.ensure_schema()
    except db.SchemaMissing as err:
        print(f"❌ {err}")
        return 2
    run = db.get_run(args.run)
    if not run:
        print(f"❌ run {args.run} not found")
        return 2
    window = (str(run["window_start"]), str(run["window_end"]))
    urls, source = ticked_urls(args.run)
    if source == "suggestions":
        logger.warning("⚠️ no applied review for %s → %s — using the engine's %d suggested picks", *window, len(urls))
    else:
        logger.info("✅ %d ticked URL(s) from the applied review (%s → %s)", len(urls), *window)
    until = window_until(args.run)
    line = watermark_line(until, window=window) if until else None

    if args.open and args.dry_run:
        res = open_in_chrome(urls, dry_run=True)
        print(f"🌐 dry run: would open {res['would_open']} tab(s), {res['skipped_open']} already open — nothing opened")
    elif args.open:
        res = open_in_chrome(urls)
        print(f"🌐 opened {res['opened']} tab(s), {res['skipped_open']} already open — now run `newsletter_pipeline.py archive`")
    else:
        print(f"🌐 would open {len(urls)} tab(s) in the :9222 Chrome (--open):")
        for u in urls:
            print("   ", u)
    if line is None:
        print("📝 no e-mails in this run — no watermark line")
    elif args.mark_reviewed:
        res = mark_reviewed(args.run, line=line)
        print(f"📝 comment {res['comment_id']} written · reviewed_until = {res['reviewed_until']}")
    else:
        print(f"📝 watermark line (paste on the task page, or re-run with --mark-reviewed): {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
