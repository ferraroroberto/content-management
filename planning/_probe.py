"""Live-DOM probe for the planning schedulers — shared by the self-heal skill
and by humans debugging a selector break by hand.

When a platform's UI drifts (an ``aria-label`` appears, a button is renamed, a
header is retyped), the fix is always to find the element's *new* accessible
name and re-anchor the role/text selector on it. This module opens the live
page through the platform's existing session helper (real Chrome, stealth,
shared-profile lock-wait — never re-inlining launch args) and dumps the
accessibility tree as a ranked list of ``role + name`` candidates, which is
exactly the shape a Playwright ``get_by_role(role, name=...)`` selector needs.

Class-based candidates are deliberately not emitted — the per-platform READMEs
warn that class names rotate, so a fix anchored on a class would re-break next
week. Role + accessible name is the durable anchor.

CLI::

    python -m planning._probe twitter
    python -m planning._probe linkedin --url https://www.linkedin.com/feed/
    python -m planning._probe threads --no-screenshot
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from typing import Optional

# platform -> (module, session-class, config-loader). Lazy-imported so probing
# one platform never drags in the other three (or Playwright) unnecessarily.
_REGISTRY: dict[str, tuple[str, str, str]] = {
    "twitter": ("planning.twitter.twitter_session", "TwitterSession", "load_twitter_config"),
    "threads": ("planning.threads.threads_session", "ThreadsSession", "load_threads_config"),
    "instagram": ("planning.instagram.instagram_session", "InstagramSession", "load_instagram_config"),
    "linkedin": ("planning.linkedin.linkedin_session", "LinkedInSession", "load_linkedin_config"),
}

# Roles that back a clickable/typeable selector — these are what a heal edit
# re-anchors on, so they sort to the top of the dump.
_INTERACTIVE_ROLES = (
    "button", "link", "textbox", "menuitem", "tab", "checkbox",
    "combobox", "switch", "radio", "option", "searchbox",
)
# Structural roles worth seeing (dialogs / headings frame the failing region).
_CONTEXT_ROLES = ("dialog", "heading", "alertdialog", "menu", "listbox")


# Matches a top-level node line in Playwright's ``aria_snapshot()`` YAML dump,
# e.g. `- button "Submit"` or `- link "Home Link":`. Nested non-ARIA property
# lines (e.g. `- /url: "#"`) start with a non-letter and don't match.
_ARIA_NODE_RE = re.compile(r'^\s*-\s+([a-zA-Z][\w-]*)\s+"([^"]*)"')


def _parse_aria_snapshot(snapshot: str) -> list[tuple[str, str]]:
    """Collect ``(role, name)`` pairs from a Playwright ``aria_snapshot()`` dump.

    ``aria_snapshot()`` renders the accessibility tree as indented YAML text
    rather than the nested-dict shape the old (removed) ``page.accessibility``
    API returned, so this parses lines instead of walking a tree. Order is
    preserved top-to-bottom, matching the previous depth-first walk.
    """
    pairs: list[tuple[str, str]] = []
    for line in snapshot.splitlines():
        m = _ARIA_NODE_RE.match(line)
        if not m:
            continue
        role, name = m.group(1).strip(), m.group(2).strip()
        if role and name:
            pairs.append((role, name))
    return pairs


def _format(url: str, title: str, pairs: list[tuple[str, str]]) -> str:
    interactive = [(r, n) for r, n in pairs if r in _INTERACTIVE_ROLES]
    context = [(r, n) for r, n in pairs if r in _CONTEXT_ROLES]

    # De-dupe while preserving order.
    def _dedupe(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    interactive = _dedupe(interactive)
    context = _dedupe(context)

    lines = [
        f"# DOM probe — {url}",
        f"# title: {title}",
        f"# {len(interactive)} interactive candidate(s), {len(context)} context node(s)",
        "",
        "## Interactive candidates (role + accessible name -> get_by_role)",
    ]
    if interactive:
        for role, name in interactive:
            lines.append(f'  {role:<10} name={name!r}')
    else:
        lines.append("  (none — page may not have loaded the target region yet)")
    lines.append("")
    lines.append("## Context nodes (dialogs / headings)")
    if context:
        for role, name in context:
            lines.append(f'  {role:<10} name={name!r}')
    else:
        lines.append("  (none)")
    lines.append("")
    return "\n".join(lines)


def probe(platform: str, url: Optional[str] = None, *, save_screenshot: bool = True) -> str:
    """Open ``platform``'s live page and return a ranked role+name candidate dump.

    ``url`` defaults to the platform's configured ``feed_url`` (the same entry
    point the scheduler uses). Raises ``KeyError`` for an unknown platform and
    surfaces the session helper's ``LoginRequiredError`` unchanged so the caller
    can tell a logged-out session apart from real UI drift.
    """
    key = platform.lower()
    if key not in _REGISTRY:
        raise KeyError(f"unknown platform {platform!r}; expected one of {sorted(_REGISTRY)}")
    module_path, cls_name, loader_name = _REGISTRY[key]
    module = importlib.import_module(module_path)
    session_cls = getattr(module, cls_name)
    cfg = getattr(module, loader_name)()
    target = url or cfg.get("feed_url")
    if not target:
        raise RuntimeError(f"no url given and no 'feed_url' in {key} config")

    with session_cls(cfg) as session:
        session.goto_with_login_check(target)
        snapshot = session.page.locator("body").aria_snapshot()
        title = session.page.title()
        pairs = _parse_aria_snapshot(snapshot) if snapshot else []
        if save_screenshot:
            session.screenshot_failure(f"probe-{key}")
        return _format(target, title, pairs)


def main() -> int:
    # Force UTF-8 stdout so emoji / em-dash in the dump don't crash a cp1252
    # Windows console (same guard the session modules use).
    from config.console import force_utf8_stdio
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Dump live-DOM role+name candidates for a planning platform.")
    parser.add_argument("platform", choices=sorted(_REGISTRY), help="which platform's live page to probe")
    parser.add_argument("--url", default=None, help="override the page URL (defaults to the platform's feed_url)")
    parser.add_argument("--no-screenshot", action="store_true", help="skip saving a probe screenshot")
    args = parser.parse_args()
    print(probe(args.platform, args.url, save_screenshot=not args.no_screenshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
