#!/usr/bin/env python3
"""Newsletter HTML builder.

Given a newsletter number (e.g. ``057`` or ``N057``), pulls every article
related to that newsletter, groups by topic (personal development /
innovation / leadership and management), sorts each group (star desc →
niche asc → title asc), emits HTML to
``results/newsletter/N{NNN}.html``, opens it in the default browser, then
prompts for the "must-read" topic and copies the composed line to the
clipboard.

Originally from ``E:\\automation\\automation\\notion\\build_newsletter.py``;
migrated into the newsletter package as part of issue #18. Config now
comes from ``config/config.json`` (Notion token, articles + newsletter DB
ids); HTML output landed under ``results/newsletter/`` instead of next to
the script.

CLI:
    python -m newsletter.build_newsletter --newsletter 057
    python -m newsletter.build_newsletter                # interactive prompt
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_CONFIG = REPO_ROOT / "config" / "config.json"
RESULTS_DIR = REPO_ROOT / "results" / "newsletter"

TOPICS: List[str] = [
    "personal development",
    "innovation",
    "leadership and management",
]
TOPIC_HEADINGS: List[str] = [
    "Personal development",
    "Innovation",
    "Leadership and management",
]
_MUST_READ_PERM: Dict[int, Tuple[int, int, int]] = {
    1: (0, 1, 2),
    2: (1, 0, 2),
    3: (2, 0, 1),
}


class NotionNewsletterBuilder:
    def __init__(self):
        self.config = self._load_config()
        self.notion_api_key = self.config["notion_api_key"]
        self.articles_db_id = self.config["articles_db_id"]
        self.newsletter_db_id = self.config["newsletter_db_id"]
        if not all([self.notion_api_key, self.articles_db_id, self.newsletter_db_id]):
            raise ValueError("Missing notion_api_key or DB ids in config")
        self.headers = {
            "Authorization": f"Bearer {self.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        self.topics = TOPICS
        logging.info("✅ Newsletter builder initialized")
        logging.info(f"📊 Articles DB: {self.articles_db_id}")
        logging.info(f"📊 Newsletter DB: {self.newsletter_db_id}")

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        with PROJECT_CONFIG.open("r", encoding="utf-8") as f:
            proj = json.load(f)
        archive = proj.get("newsletter_archive", {})
        return {
            "notion_api_key": proj["notion"]["api_token"],
            "articles_db_id": archive["articles_db_id"],
            "newsletter_db_id": archive["newsletter_db_id"],
        }

    def _query_notion_database(self, database_id: str,
                               filter_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        payload: Dict[str, Any] = {}
        if filter_data:
            payload["filter"] = filter_data
        all_results: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            if cursor:
                payload["start_cursor"] = cursor
            try:
                resp = requests.post(url, headers=self.headers, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                logging.error(f"❌ Failed to query Notion: {e}")
                raise
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return all_results

    def find_newsletter_by_title(self, newsletter_title: str) -> Optional[Dict[str, Any]]:
        logging.info(f"🔍 Searching for newsletter: {newsletter_title}")
        filter_data = {"property": "number", "title": {"equals": newsletter_title}}
        results = self._query_notion_database(self.newsletter_db_id, filter_data)
        if not results:
            logging.error(f"❌ No newsletter found with number: {newsletter_title}")
            return None
        logging.info(f"✅ Found newsletter: {newsletter_title}")
        return results[0]

    def get_related_articles(self, newsletter_id: str) -> List[Dict[str, Any]]:
        logging.info(f"📥 Fetching articles for newsletter id: {newsletter_id}")
        filter_data = {"property": "news", "relation": {"contains": newsletter_id}}
        results = self._query_notion_database(self.articles_db_id, filter_data)
        logging.info(f"📊 Found {len(results)} articles")
        return results

    @staticmethod
    def extract_article_data(article: Dict[str, Any]
                             ) -> Optional[Tuple[str, str, str, bool, List[str]]]:
        try:
            props = article.get("properties", {})
            title_prop = props.get("article", {})
            if title_prop.get("type") != "title":
                return None
            title_content = title_prop.get("title", [])
            if not title_content:
                return None
            name = title_content[0].get("plain_text", "").strip()

            url_prop = props.get("link", {})
            if url_prop.get("type") != "url":
                return None
            url = (url_prop.get("url") or "").strip()
            if not url:
                return None

            topic_prop = props.get("topic", {})
            if topic_prop.get("type") != "select":
                return None
            topic_obj = topic_prop.get("select")
            if not topic_obj:
                return None
            topic = topic_obj.get("name", "").strip()

            star_prop = props.get("star", {})
            star = bool(star_prop.get("checkbox", False)) if star_prop.get("type") == "checkbox" else False

            niche_prop = props.get("niche", {})
            niche: List[str] = []
            if niche_prop.get("type") == "multi_select":
                niche = [o.get("name", "").strip() for o in niche_prop.get("multi_select", []) if o.get("name")]
            return name, url, topic, star, niche
        except Exception as e:
            logging.error(f"❌ Error extracting article data: {e}")
            return None

    def group_articles_by_topic(self, articles: List[Dict[str, Any]]
                                ) -> Dict[str, List[Tuple[str, str]]]:
        grouped: Dict[str, List[Tuple[str, str, bool, List[str]]]] = {t: [] for t in self.topics}
        for art in articles:
            data = self.extract_article_data(art)
            if not data:
                continue
            name, url, topic, star, niche = data
            if topic in self.topics:
                grouped[topic].append((name, url, star, niche))

        for topic in self.topics:
            grouped[topic].sort(key=lambda x: (
                -x[2],
                sorted(x[3])[0] if x[3] else "",
                x[0].lower(),
            ))
            if grouped[topic]:
                logging.info(f"📋 Topic '{topic}' sorted order:")
                for i, (name, _url, star, niche) in enumerate(grouped[topic], 1):
                    niche_str = ", ".join(sorted(niche)) if niche else "none"
                    star_str = "⭐" if star else "⚪"
                    logging.info(f"  {i}. {star_str} {name} (niche: {niche_str})")

        return {t: [(n, u) for n, u, _s, _ni in grouped[t]] for t in self.topics}

    def generate_html_lists(self, grouped: Dict[str, List[Tuple[str, str]]]) -> str:
        out: List[str] = []
        for topic in self.topics:
            heading = topic[0].upper() + topic[1:]
            out.append(f"<h2>{heading}</h2>")
            articles = grouped[topic]
            if articles:
                out.append("<ul>")
                for name, url in articles:
                    out.append(f'  <li><a href="{url}">{name}</a></li>')
                out.append("</ul>")
            else:
                out.append("<ul></ul>")
            out.append("")
        return "\n".join(out)

    def generate_complete_html(self, grouped: Dict[str, List[Tuple[str, str]]]) -> str:
        html_content = self.generate_html_lists(grouped)
        return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Newsletter Content</title>
    <style>
        body {{ background-color: black; color: white; font-family: Arial, sans-serif; margin: 20px; }}
        h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 10px; }}
        a {{ color: #4a9eff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        ul {{ margin: 0; padding-left: 20px; }}
        li {{ margin: 8px 0; }}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""

    def build_newsletter(self, newsletter_title: str
                         ) -> Tuple[str, Dict[str, List[Tuple[str, str]]]]:
        logging.info(f"🚀 Building newsletter: {newsletter_title}")
        nl = self.find_newsletter_by_title(newsletter_title)
        if not nl:
            raise ValueError(f"Newsletter '{newsletter_title}' not found")
        articles = self.get_related_articles(nl["id"])
        if not articles:
            raise ValueError(f"No articles found for newsletter '{newsletter_title}'")
        grouped = self.group_articles_by_topic(articles)
        return self.generate_html_lists(grouped), grouped


# ---------------------------------------------------------------- helpers


def top_article_names_by_topic(
    topics: Sequence[str], grouped: Dict[str, List[Tuple[str, str]]]
) -> Optional[List[str]]:
    names: List[str] = []
    for topic in topics:
        articles = grouped.get(topic) or []
        if not articles:
            return None
        names.append(articles[0][0])
    return names


def format_must_read_line(three_names: Sequence[str], must_read: int) -> str:
    perm = _MUST_READ_PERM[must_read]
    return ". ".join(three_names[i] for i in perm) + "."


def topics_sidecar_path(newsletter_number: str) -> Path:
    """Path of the topics sidecar JSON for a newsletter number (``057``/``N057``)."""
    return RESULTS_DIR / f"{_normalize_newsletter_number(newsletter_number)}.topics.json"


def _write_topics_sidecar(
    nl_num: str,
    headings: Sequence[str],
    top_names: Optional[Sequence[str]],
) -> Path:
    """Persist the top article per topic so the Streamlit must-read picker can
    compose the line without re-querying Notion (the app drives builds as
    subprocesses, so there is no structured return value to read).

    ``top_names`` is ``None`` when any topic has no articles — the UI then knows
    the must-read line is unavailable.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = topics_sidecar_path(nl_num)
    payload = {
        "newsletter": nl_num,
        "headings": list(headings),
        "top_names": list(top_names) if top_names is not None else None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def copy_to_clipboard(text: str) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["clip"],
            input=text, text=True, encoding="utf-8", check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()
    root.update()
    root.after(100, root.destroy)
    root.mainloop()


def prompt_must_read_line(topics: Sequence[str],
                          grouped: Dict[str, List[Tuple[str, str]]]) -> Optional[str]:
    top = top_article_names_by_topic(topics, grouped)
    if not top:
        for topic in topics:
            if not (grouped.get(topic) or []):
                logging.warning(f'⚠️ Skipping must-read line: no articles in "{topic}"')
        return None
    logging.info("📌 Top article per topic (1=Personal dev, 2=Innovation, 3=Leadership):")
    for i, (heading, name) in enumerate(zip(TOPIC_HEADINGS, top), 1):
        logging.info(f"  {i}. [{heading}] {name}")
    try:
        raw = input('Which is the "must read"? (1/2/3): ')
    except (EOFError, KeyboardInterrupt):
        logging.info("⏭️ Skipping must-read line (no input)")
        return None
    if raw.strip() not in ("1", "2", "3"):
        logging.error("❌ Must be 1, 2, or 3")
        return None
    line = format_must_read_line(top, int(raw.strip()))
    copy_to_clipboard(line)
    logging.info(f"📋 Must-read line (copied to clipboard): {line}")
    return line


# ---------------------------------------------------------------- entry points


def _normalize_newsletter_number(raw: str) -> str:
    val = raw.strip().upper()
    if re.fullmatch(r"\d{3}", val):
        return f"N{val}"
    if re.fullmatch(r"N\d{3}", val):
        return val
    raise ValueError("Newsletter number must be 3 digits (057) or N + 3 digits (N057)")


def run(newsletter_number: str, debug: bool = False, *,
        interactive_must_read: bool = True, open_browser: bool = True,
        must_read: Optional[int] = None) -> Path:
    """Build the newsletter end-to-end. Returns the HTML file path written.

    Always writes a topics sidecar (``N{NNN}.topics.json``) next to the HTML so
    the Streamlit must-read picker can compose the line without re-querying
    Notion.

    Must-read handling, in priority order:

    * ``must_read`` (1/2/3) set → compose + copy the line non-interactively.
    * else ``interactive_must_read`` → prompt on stdin (console flow).
    * else → write the HTML + sidecar only (the app's non-blocking path).
    """
    _setup_logging(debug)
    nl_num = _normalize_newsletter_number(newsletter_number)
    builder = NotionNewsletterBuilder()
    _, grouped = builder.build_newsletter(nl_num)

    complete_html = builder.generate_complete_html(grouped)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{nl_num}.html"
    out_path.write_text(complete_html, encoding="utf-8")
    logging.info(f"💾 HTML saved to: {out_path}")

    top_names = top_article_names_by_topic(builder.topics, grouped)
    sidecar = _write_topics_sidecar(nl_num, TOPIC_HEADINGS, top_names)
    logging.info(f"🗂️ Topics sidecar: {sidecar}")

    if open_browser:
        webbrowser.open(f"file://{out_path}")
        logging.info(f"🌐 Opened HTML in browser: {out_path}")

    if must_read is not None:
        if top_names is None:
            logging.warning("⚠️ Cannot compose must-read line: a topic has no articles")
        else:
            line = format_must_read_line(top_names, must_read)
            copy_to_clipboard(line)
            logging.info(f"📋 Must-read line (copied to clipboard): {line}")
    elif interactive_must_read:
        prompt_must_read_line(builder.topics, grouped)

    return out_path


def _setup_logging(debug: bool = False):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--newsletter", type=str,
                        help='Newsletter number, e.g. "057" or "N057". '
                        'Prompted if omitted.')
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    _setup_logging(args.debug)
    try:
        nl = args.newsletter
        if not nl:
            try:
                nl = input("Enter newsletter number (e.g. 057): ")
            except (EOFError, KeyboardInterrupt):
                logging.error("❌ Newsletter number input cancelled")
                return 2
        if not nl:
            logging.error("❌ Newsletter number is required")
            return 2
        run(nl, debug=args.debug)
        return 0
    except (ValueError, FileNotFoundError, requests.exceptions.RequestException) as e:
        logging.error(f"❌ Failed to build newsletter: {e}")
        return 1
    except Exception as e:
        logging.error(f"❌ Unexpected error: {e}")
        if args.debug:
            logging.exception("Traceback:")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
