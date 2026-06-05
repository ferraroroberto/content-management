r"""Manual CLI — create a newsletter edition as a DRAFT, optionally publish (P1).

Uses the native HTTP API (cookie auth). NOT part of the daily cron.

By default this creates a **private draft** and runs Substack's pre-publish
validation — it does NOT send anything to subscribers. Publishing (which emails
the whole list and is irreversible) happens only with the explicit ``--confirm``
flag.

Usage (from the repo root):
    & .\.venv\Scripts\python.exe -m planning.substack.api_create \
        --title "..." --subtitle "..." \
        (--body "para 1" --body "para 2" | --markdown-file post.md) \
        [--heading "..."] [--image path.png] [--confirm] [--delete-after]

    --body TEXT        a paragraph (repeatable; inline markdown supported)
    --markdown-file F  read the body from a markdown file instead of --body
    --heading TEXT     optional H2 heading at the top
    --image PATH       optional image (uploaded, appended)
    --confirm          DANGER: actually publish + email subscribers
    --delete-after     delete the draft after creating it (smoke-test only)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from planning.substack.api_client import SubstackAPI
from planning.substack.substack_session import configure_logger, load_substack_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create (and optionally publish) a Substack edition.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--body", action="append", default=[], help="A paragraph (repeatable).")
    parser.add_argument("--markdown-file", type=str, default=None, help="Read body paragraphs from a file.")
    parser.add_argument("--heading", type=str, default=None)
    parser.add_argument("--image", type=str, default=None)
    parser.add_argument("--confirm", action="store_true", help="DANGER: publish + email subscribers.")
    parser.add_argument("--delete-after", action="store_true", help="Delete the draft after creating (smoke test).")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def _resolve_paragraphs(args: argparse.Namespace) -> list[str]:
    if args.markdown_file:
        text = Path(args.markdown_file).read_text(encoding="utf-8")
        return [block.strip() for block in text.split("\n\n") if block.strip()]
    return list(args.body)


def main() -> int:
    args = parse_args()
    logger = configure_logger("substack_api_create", debug=args.debug)
    cfg = load_substack_config()

    paragraphs = _resolve_paragraphs(args)
    if not paragraphs:
        logger.error("❌ No body content — pass --body (repeatable) or --markdown-file.")
        return 2

    api = SubstackAPI(publication_url=cfg.get("publish_url"))
    draft = api.create_draft(
        title=args.title,
        subtitle=args.subtitle,
        paragraphs=paragraphs,
        heading=args.heading,
        image_path=args.image,
    )
    draft_id = draft.get("id")
    logger.info("✅ Draft created — id=%s", draft_id)

    verdict = api.prepublish(draft_id)
    errors = verdict.get("errors") if isinstance(verdict, dict) else None
    logger.info("🔎 Prepublish — errors=%s", errors or "none")

    if args.delete_after:
        api.delete_draft(draft_id)
        logger.info("🗑️ Draft %s deleted (smoke test).", draft_id)
        return 0

    if args.confirm:
        logger.warning("🚨 --confirm passed — publishing and emailing subscribers!")
        result = api.publish(draft_id, send=True)
        logger.info("📣 Published — id=%s", result.get("id"))
    else:
        edit_url = f"{cfg.get('publish_url', '').rsplit('/publish/', 1)[0]}/publish/post/{draft_id}"
        logger.info("🛑 Not publishing (no --confirm). Review/edit the draft, then publish from Substack:")
        logger.info("   %s", edit_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
