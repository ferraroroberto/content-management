r"""Reputation feedback loop — Phase 3.

Recomputes per-commenter rolling signals + counters + reputation_score from
the `comments` table and upserts them back into `commenters`. Pure batch and
idempotent — re-running is safe.

Truth-of-record is Supabase: every score / counter / signal lives in
`commenters.{signals, counters, reputation_score}`. The existing manual
classification (whitelist / blacklist / unknown) is **never overwritten** —
this job preserves it and merely pins the score to the right ±1.0 when set.

CLI:
    & .\.venv\Scripts\python.exe -m engagement.reputation.update --platform linkedin
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

from engagement.classify.rules import (
    _build_duplicate_set,
    _generic_praise_hits,
    _seconds_after,
    load_phrases,
)
from engagement.db.client import supabase_client

logger = logging.getLogger("engagement.reputation")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _classification_for(commenters: dict, account_url: str) -> str:
    row = commenters.get(account_url) or {}
    return row.get("classification", "unknown") or "unknown"


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _score_from_signals(counters: dict, classification: str) -> float:
    """Blend rolling counters into a reputation score in [-1.0, 1.0].

    Whitelist / blacklist hard-pin to ±1.0 — the human label always wins.
    Everything else is a soft weighted blend of accumulated behaviour:
      + verdict balance (human - ai) / total
      − generic-praise rate
      − sub-2-min cadence rate
      − exact-duplicate text rate
    """
    if classification == "whitelist":
        return 1.0
    if classification == "blacklist":
        return -1.0

    total = max(1, int(counters.get("comments_seen", 0)))
    human = int(counters.get("human_verdicts", 0))
    ai = int(counters.get("ai_verdicts", 0))
    praise = int(counters.get("generic_praise_hits", 0))
    sub2 = int(counters.get("sub_2_min_count", 0))
    dup = int(counters.get("exact_dup_count", 0))

    score = 0.0
    score += 0.4 * ((human - ai) / total)
    score -= 0.2 * (praise / total)
    score -= 0.2 * (sub2 / total)
    score -= 0.2 * (dup / total)
    return _clamp(score)


def _aggregate(
    rows: list[dict],
    duplicates: set,
    phrases: dict,
) -> tuple[dict, dict, Optional[datetime], Optional[str]]:
    """Aggregate one commenter's comment rows into (counters, signals, last_seen, display_name)."""
    counters = {
        "comments_seen": 0,
        "unique_posts_commented": 0,
        "generic_praise_hits": 0,
        "sub_2_min_count": 0,
        "exact_dup_count": 0,
        "human_verdicts": 0,
        "ai_verdicts": 0,
    }
    lengths: list[int] = []
    seconds_to_comment: list[float] = []
    posts: set[str] = set()
    posted_ats: list[datetime] = []
    display_name: Optional[str] = None

    praise_terms = phrases["generic_praise_substrings"]

    for r in rows:
        text = (r.get("text") or "").strip()
        text_norm = text.lower()
        counters["comments_seen"] += 1
        lengths.append(len(text))
        if r.get("post_url"):
            posts.add(r["post_url"])
        if _generic_praise_hits(text_norm, praise_terms) > 0:
            counters["generic_praise_hits"] += 1
        secs = _seconds_after(r.get("post_posted_at"), r.get("posted_at"))
        if secs is not None:
            seconds_to_comment.append(secs)
            if secs <= 120:
                counters["sub_2_min_count"] += 1
        if text_norm and text_norm in duplicates:
            counters["exact_dup_count"] += 1
        cls = r.get("classification")
        if cls == "human":
            counters["human_verdicts"] += 1
        elif cls == "ai":
            counters["ai_verdicts"] += 1
        posted = _parse_iso(r.get("posted_at")) or _parse_iso(r.get("scraped_at"))
        if posted is not None:
            posted_ats.append(posted)
        if not display_name and r.get("display_name"):
            display_name = r["display_name"]

    counters["unique_posts_commented"] = len(posts)

    signals = {
        "mean_text_len": (sum(lengths) / len(lengths)) if lengths else 0.0,
        "length_stdev": float(statistics.pstdev(lengths)) if len(lengths) >= 2 else 0.0,
        "median_seconds_to_comment": (
            float(statistics.median(seconds_to_comment)) if seconds_to_comment else None
        ),
    }
    last_seen = max(posted_ats) if posted_ats else None
    return counters, signals, last_seen, display_name


def recompute_for_platform(platform: str = "linkedin") -> dict:
    sb = supabase_client()

    # Note: `post_posted_at` is a scrape-time side-channel (stripped before
    # upsert) — it isn't a column in the `comments` table. The cadence
    # "seconds after my post" signal will simply be absent until that gets
    # backfilled; for now `_seconds_after` returns None and the median
    # seconds + sub-2-min counter stay at zero, matching rules.py behaviour.
    rows = (
        sb.table("comments")
        .select("comment_id,commenter_url,display_name,text,post_url,posted_at,scraped_at,classification")
        .eq("platform", platform)
        .execute()
        .data
        or []
    )
    logger.info("📊 reputation: %d comment rows for %s", len(rows), platform)

    if not rows:
        return {"platform": platform, "commenters_updated": 0, "comments_seen": 0}

    phrases = load_phrases()
    duplicates = _build_duplicate_set(rows)

    by_url: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        url = r.get("commenter_url")
        if url:
            by_url[url].append(r)

    # Pull existing classification + signals once so the upsert preserves
    # manual labels (whitelist/blacklist) and merges into the existing signals
    # jsonb rather than wiping any keys we don't compute here.
    existing_rows = (
        sb.table("commenters")
        .select("account_url,classification,signals")
        .eq("platform", platform)
        .in_("account_url", list(by_url.keys()))
        .execute()
        .data
        or []
    )
    existing = {r["account_url"]: r for r in existing_rows}

    payloads: list[dict] = []
    for url, group in by_url.items():
        counters, signals, last_seen, display_name = _aggregate(group, duplicates, phrases)
        classification = _classification_for(existing, url)
        score = _score_from_signals(counters, classification)

        # Merge into existing signals jsonb so any keys we don't compute (set
        # by a future signal extractor) survive.
        merged_signals = dict((existing.get(url) or {}).get("signals") or {})
        merged_signals.update(signals)

        payload = {
            "platform": platform,
            "account_url": url,
            "reputation_score": score,
            "signals": merged_signals,
            "counters": counters,
        }
        if last_seen is not None:
            payload["last_seen"] = last_seen.isoformat()
        if display_name and url not in existing:
            # Only seed display_name on first insert — avoid clobbering a
            # corrected name on an existing row.
            payload["display_name"] = display_name
        payloads.append(payload)

    if payloads:
        sb.table("commenters").upsert(payloads, on_conflict="platform,account_url").execute()

    logger.info(
        "✅ reputation: upserted %d commenters across %d comments",
        len(payloads), len(rows),
    )
    return {
        "platform": platform,
        "commenters_updated": len(payloads),
        "comments_seen": len(rows),
    }


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Recompute commenter reputation signals.")
    parser.add_argument("--platform", default="linkedin")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    result = recompute_for_platform(args.platform)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
