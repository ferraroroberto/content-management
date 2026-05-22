"""Rule-based classifier for the engagement pipeline.

Pipeline (per pending comment):
1. Whitelist hit on the commenter → classification=human, surface_to_me.
2. Blacklist hit on the commenter → classification=ai, like_and_thanks.
3. Otherwise score against rules in `phrases.json`; if score ≥ threshold
   → ai/like_and_thanks; else → unknown/surface_to_me (bias to human review).

All thresholds and phrase lists live in `engagement/classify/phrases.json`
so tuning never requires a code change.
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from engagement.db.client import (
    fetch_commenters_by_urls,
    load_engagement_config,
    supabase_client,
)

logger = logging.getLogger("engagement.classify")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHRASES_PATH = REPO_ROOT / "engagement" / "classify" / "phrases.json"


def load_phrases() -> dict:
    with open(PHRASES_PATH, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _first_name(display_name: Optional[str]) -> str:
    if not display_name:
        return "there"
    return display_name.strip().split()[0]


def _pick_reply(action: str, display_name: Optional[str], phrases: dict) -> Optional[str]:
    if action != "like_and_thanks":
        return None
    templates = phrases.get("reply_templates", {}).get("like_and_thanks", [])
    if not templates:
        return f"Thanks {_first_name(display_name)}! 🙏"
    return random.choice(templates).format(first_name=_first_name(display_name))


def _generic_praise_hits(text: str, phrases: list[str]) -> int:
    t = text.lower()
    return sum(1 for p in phrases if p.lower() in t)


def _has_personal_token(text: str, tokens: list[str]) -> bool:
    t = text.lower()
    return any(tok in t for tok in tokens)


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)


def _is_emoji_only(text: str) -> bool:
    if not text:
        return False
    stripped = _EMOJI_RE.sub("", text).strip()
    return stripped == ""


def _seconds_after(post_at: Optional[str], commented_at: Optional[str]) -> Optional[float]:
    if not post_at or not commented_at:
        return None
    try:
        a = datetime.fromisoformat(post_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(commented_at.replace("Z", "+00:00"))
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return None


def _score_comment(
    comment: dict,
    commenter: Optional[dict],
    duplicate_texts: set[str],
    phrases: dict,
) -> tuple[float, list[dict]]:
    rules = phrases["rules"]
    reasons: list[dict] = []
    score = 0.0

    text = (comment.get("text") or "").strip()
    text_norm = text.lower()

    hits = _generic_praise_hits(text_norm, phrases["generic_praise_substrings"])
    if hits >= rules["generic_praise_threshold_hits"]:
        score += rules["generic_praise_weight"]
        reasons.append({"rule": "generic_praise", "hits": hits, "weight": rules["generic_praise_weight"]})

    if len(text) <= rules["short_comment_max_chars"]:
        score += rules["short_comment_weight"]
        reasons.append({"rule": "short_comment", "len": len(text), "weight": rules["short_comment_weight"]})

    if not _has_personal_token(text_norm, phrases["personal_tokens"]):
        score += rules["no_personal_token_weight"]
        reasons.append({"rule": "no_personal_token", "weight": rules["no_personal_token_weight"]})

    if _is_emoji_only(text):
        score += rules["emoji_only_weight"]
        reasons.append({"rule": "emoji_only", "weight": rules["emoji_only_weight"]})

    if text_norm and text_norm in duplicate_texts:
        score += rules["exact_text_duplicate_weight"]
        reasons.append({"rule": "exact_text_duplicate", "weight": rules["exact_text_duplicate_weight"]})

    secs = _seconds_after(comment.get("post_posted_at"), comment.get("posted_at"))
    if secs is not None and secs <= 120:
        score += rules["sub_2_min_weight"]
        reasons.append({"rule": "sub_2_min", "seconds": secs, "weight": rules["sub_2_min_weight"]})

    return score, reasons


def _build_duplicate_set(comments: list[dict]) -> set[str]:
    """Return set of (lowercased) texts that appear from >=2 distinct commenters."""
    by_text: dict[str, set[str]] = {}
    for c in comments:
        t = (c.get("text") or "").strip().lower()
        if not t:
            continue
        by_text.setdefault(t, set()).add(c.get("commenter_url") or "")
    return {t for t, urls in by_text.items() if len(urls) >= 2}


def classify_pending(platform: str = "linkedin") -> dict:
    """Score every `status=pending` `classification=unknown` row and write the verdict back."""
    phrases = load_phrases()
    cfg = load_engagement_config()
    threshold = phrases["rules"]["ai_classification_threshold"]

    sb = supabase_client()
    pending = (
        sb.table("comments")
        .select("*")
        .eq("platform", platform)
        .eq("status", "pending")
        .eq("classification", "unknown")
        .execute()
        .data
        or []
    )
    logger.info("🧮 classifying %d pending rows for %s", len(pending), platform)

    if not pending:
        return {"scanned": 0, "ai": 0, "human": 0, "unknown": 0}

    commenter_urls = {c["commenter_url"] for c in pending if c.get("commenter_url")}
    commenters = fetch_commenters_by_urls(platform, commenter_urls)
    duplicates = _build_duplicate_set(pending)

    counts = Counter()
    updates: list[dict] = []
    for c in pending:
        commenter = commenters.get(c.get("commenter_url") or "")
        classification = commenter.get("classification", "unknown") if commenter else "unknown"

        if classification == "whitelist":
            verdict = ("human", 1.0, "whitelist", [{"rule": "whitelist"}], "surface_to_me", None)
        elif classification == "blacklist":
            verdict = (
                "ai", 1.0, "blacklist",
                [{"rule": "blacklist"}],
                "like_and_thanks",
                _pick_reply("like_and_thanks", c.get("display_name"), phrases),
            )
        else:
            score, reasons = _score_comment(c, commenter, duplicates, phrases)
            if score >= threshold:
                verdict = (
                    "ai", score, "rules",
                    reasons,
                    "like_and_thanks",
                    _pick_reply("like_and_thanks", c.get("display_name"), phrases),
                )
            else:
                # Stay unknown so we still surface for review, but record the score.
                verdict = ("unknown", score, "rules", reasons, "surface_to_me", None)

        classification_new, conf, src, reasons, action, reply = verdict
        counts[classification_new] += 1
        payload = {
            "platform": c["platform"],
            "comment_id": c["comment_id"],
            "classification": classification_new,
            "confidence": conf,
            "verdict_source": src,
            "verdict_reasons": reasons,
            "suggested_action": action,
            "suggested_reply": reply,
        }
        # Blacklist-classified comments auto-approve — same policy as
        # cascade_blacklist_pending so the manual-click and the rescan
        # paths give identical state. User trusts the blacklist.
        if src == "blacklist":
            payload["status"] = "approved"
            payload["decided_at"] = "now()"
        updates.append(payload)

    # Apply updates one row at a time (PostgREST has no batch-update-by-pk).
    # ~hundreds of rows max per run, so individual updates are fine.
    for u in updates:
        sb.table("comments").update(
            {k: v for k, v in u.items() if k not in ("platform", "comment_id")}
        ).eq("platform", u["platform"]).eq("comment_id", u["comment_id"]).execute()

    logger.info(
        "✅ classify done — ai=%d unknown=%d human=%d (threshold=%.2f)",
        counts["ai"], counts["unknown"], counts["human"], threshold,
    )
    return {"scanned": len(pending), **counts}


def main() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Classify pending engagement comments.")
    parser.add_argument("--platform", default="linkedin")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    result = classify_pending(args.platform)
    print(result)


if __name__ == "__main__":
    main()
