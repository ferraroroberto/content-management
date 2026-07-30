#!/usr/bin/env python3
"""Create a Substack DRAFT edition from a newsletter's Notion content.

Closes the last manual step of the weekly newsletter run: instead of
copy-pasting ``results/newsletter/N{NNN}.html`` into the Substack editor by
hand, this builds the same grouped article lists straight into a Substack draft
over the native HTTP API (cookie auth — no browser, no DOM selectors).

Reads exactly what ``build_newsletter`` reads (the Notion articles + newsletter
databases) and reuses its grouping/sorting, so the draft and the HTML can never
drift apart.

**Never publishes.** The draft is private and emails no one; publishing stays a
deliberate human action in the Substack editor. There is no ``--confirm`` here
by design — see ``planning/substack/api_create.py`` for the manual path that
can publish.

Requires a harvested API session (``planning/substack/api_session.json``, ~89
day cookie). If it has expired, ``SessionExpiredError`` is reported as a single
actionable line pointing at ``planning.substack.extract_session``.

CLI:
    python -m newsletter.substack_draft --newsletter 057
    python -m newsletter.substack_draft --newsletter 057 --must-read 2
    python -m newsletter.substack_draft --newsletter 057 --delete-after
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from config.loader import load_block
from newsletter.build_newsletter import (
    TOPICS,
    NotionClient,
    format_must_read_line,
    group_articles_by_topic,
    normalize_newsletter_number,
    top_article_names_by_topic,
)

# NOTE: config comes from ``config.loader`` rather than
# ``planning.substack.substack_session.load_substack_config`` on purpose — the
# latter transitively imports Playwright, and this path is pure HTTP.
from planning.substack.api_client import SessionExpiredError, SubstackAPI

Grouped = Dict[str, List[Tuple[str, str]]]
Sections = List[Tuple[str, List[Tuple[str, str]]]]


def build_sections(grouped: Grouped, topics: Sequence[str]) -> Sections:
    """Map ``{topic: [(title, url)]}`` to ``[(heading, [(title, url)])]``.

    The heading derivation mirrors ``build_newsletter.generate_html_lists`` so
    the draft's section titles match the HTML build exactly. Topics with no
    articles are kept (as an empty list) for the same reason — the HTML emits an
    empty ``<ul>`` rather than dropping the heading.

    Pure — no Notion, no Substack — so it is unit-testable on its own.
    """
    return [
        (topic[0].upper() + topic[1:], list(grouped.get(topic) or []))
        for topic in topics
    ]


def compose_intro(
    topics: Sequence[str], grouped: Grouped, must_read: Optional[int]
) -> Optional[str]:
    """The must-read line to use as the draft's opening paragraph, if available.

    ``None`` when no choice was made, or when a topic has no articles (the same
    condition under which ``build_newsletter`` reports the line unavailable).
    """
    if must_read is None:
        return None
    top_names = top_article_names_by_topic(topics, grouped)
    if top_names is None:
        logging.warning("⚠️ Cannot compose must-read line: a topic has no articles")
        return None
    return format_must_read_line(top_names, must_read)


def run(
    newsletter_number: str,
    *,
    title: Optional[str] = None,
    subtitle: str = "",
    must_read: Optional[int] = None,
    delete_after: bool = False,
    debug: bool = False,
) -> Optional[str]:
    """Build the newsletter's Substack draft. Returns the draft edit URL.

    Returns ``None`` when ``delete_after`` removed the throwaway draft.
    """
    _setup_logging(debug)
    nl_num = normalize_newsletter_number(newsletter_number)

    client = NotionClient()
    nl = client.find_newsletter_by_title(nl_num)
    if not nl:
        raise ValueError(f"Newsletter '{nl_num}' not found")
    articles = client.get_related_articles(nl["id"])
    if not articles:
        raise ValueError(f"No articles found for newsletter '{nl_num}'")
    grouped = group_articles_by_topic(articles)
    sections = build_sections(grouped, TOPICS)
    intro = compose_intro(TOPICS, grouped, must_read)

    total = sum(len(articles) for _, articles in sections)
    logging.info("📝 Building Substack draft for %s — %d articles across %d sections",
                 nl_num, total, len(sections))

    cfg = load_block("substack")
    publish_url = cfg.get("publish_url", "")
    api = SubstackAPI(publication_url=publish_url)
    draft = api.create_draft_from_sections(
        title=title or nl_num,
        subtitle=subtitle,
        sections=sections,
        intro=intro,
    )
    draft_id = draft.get("id")
    logging.info("✅ Draft created — id=%s", draft_id)

    verdict = api.prepublish(draft_id)
    errors = verdict.get("errors") if isinstance(verdict, dict) else None
    logging.info("🔎 Prepublish — errors=%s", errors or "none")

    if delete_after:
        api.delete_draft(draft_id)
        logging.info("🗑️ Draft %s deleted (smoke test).", draft_id)
        return None

    edit_url = f"{publish_url.rsplit('/publish/', 1)[0]}/publish/post/{draft_id}"
    logging.info("🛑 Draft only — nothing was sent. Review, then publish from Substack:")
    logging.info("   %s", edit_url)
    return edit_url


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    from config.console import force_utf8_stdio
    force_utf8_stdio()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    else:
        logging.getLogger().setLevel(level)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a Substack DRAFT edition from a newsletter's Notion content."
    )
    parser.add_argument("--newsletter", required=True,
                        help="Newsletter number (057 or N057).")
    parser.add_argument("--title", default=None,
                        help="Draft title (defaults to the newsletter number).")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--must-read", type=int, choices=(1, 2, 3), default=None,
                        help="Compose the must-read line as the opening paragraph.")
    parser.add_argument("--delete-after", action="store_true",
                        help="Delete the draft after creating it (smoke test).")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    try:
        run(
            args.newsletter,
            title=args.title,
            subtitle=args.subtitle,
            must_read=args.must_read,
            delete_after=args.delete_after,
            debug=args.debug,
        )
    except SessionExpiredError as err:
        logging.error("❌ %s", err)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
