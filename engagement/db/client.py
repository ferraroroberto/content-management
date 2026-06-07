"""Shared Supabase / Notion client + config loader for the engagement pipeline.

Centralises:
- loading config.json
- building the supabase-py client from config.supabase
- building the Notion client from config.notion
- upsert / select / update helpers for the `commenters` and `comments` tables

Everything else in `engagement/` imports from here so the secrets path lives
in exactly one place.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("engagement.db")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "config.json"


# ---------- Config ----------

@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        return json.load(fp)


def load_engagement_config() -> dict:
    cfg = load_config()
    block = cfg.get("engagement")
    if not block:
        raise RuntimeError("Missing 'engagement' block in config.json")
    return block


# ---------- Supabase ----------

@lru_cache(maxsize=1)
def supabase_client():
    """Return a cached supabase-py Client. Prefers service_role_key (RLS bypass);
    falls back to anon_key (works against RLS-disabled tables — which is our case
    while the engagement pipeline lives behind no public surface).
    """
    from supabase import create_client  # local import — keep optional dependency lazy

    cfg = load_config()["supabase"]
    url = cfg["url"]
    if not url:
        raise RuntimeError("Missing supabase.url in config.json")

    # Try keys in priority order; on first non-error import success, use it.
    candidates = [
        ("service_role_key", cfg.get("service_role_key")),
        ("key", cfg.get("key")),
        ("anon_key", cfg.get("anon_key")),
    ]
    last_err: Exception | None = None
    for label, key in candidates:
        if not key:
            continue
        try:
            client = create_client(url, key)
            # Cheap sanity ping — pick a table that exists, limit 1.
            client.table("commenters").select("platform").limit(1).execute()
            logger.info("🔑 using supabase key: %s", label)
            return client
        except Exception as err:
            # Trying candidates in priority order and moving on is by design, so a
            # failed candidate is not warning-worthy — it's the expected fallback
            # path. Keep the detail at DEBUG for when someone is actually debugging;
            # the terminal RuntimeError below is the single loud signal when *no*
            # key works (it carries last_err).
            last_err = err
            logger.debug("🔑 supabase key %s not usable, trying next: %s", label, err)
            continue
    raise RuntimeError(f"No working supabase key found in config.supabase (last err: {last_err})")


def upsert_commenters(rows: list[dict]) -> None:
    if not rows:
        return
    sb = supabase_client()
    sb.table("commenters").upsert(rows, on_conflict="platform,account_url").execute()
    logger.info("📥 upserted %d commenters", len(rows))


def upsert_comments(rows: list[dict]) -> None:
    if not rows:
        return
    sb = supabase_client()
    sb.table("comments").upsert(rows, on_conflict="platform,comment_id").execute()
    logger.info("📥 upserted %d comments", len(rows))


def fetch_pending_comments(platform: Optional[str] = None) -> list[dict]:
    sb = supabase_client()
    q = sb.table("comments").select("*").eq("status", "pending").order("scraped_at", desc=True)
    if platform:
        q = q.eq("platform", platform)
    return q.execute().data or []


def fetch_comments_by_status(status: str, platform: Optional[str] = None, limit: int = 500) -> list[dict]:
    sb = supabase_client()
    q = (
        sb.table("comments")
        .select("*")
        .eq("status", status)
        .order("scraped_at", desc=True)
        .limit(limit)
    )
    if platform:
        q = q.eq("platform", platform)
    return q.execute().data or []


def fetch_commenters_by_urls(platform: str, urls: Iterable[str]) -> dict[str, dict]:
    urls = list({u for u in urls if u})
    if not urls:
        return {}
    sb = supabase_client()
    rows = sb.table("commenters").select("*").eq("platform", platform).in_("account_url", urls).execute().data or []
    return {r["account_url"]: r for r in rows}


def fetch_commenter(platform: str, account_url: str) -> Optional[dict]:
    sb = supabase_client()
    rows = (
        sb.table("commenters")
        .select("*")
        .eq("platform", platform)
        .eq("account_url", account_url)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def update_comment_status(platform: str, comment_id: str, status: str, **extra: Any) -> None:
    sb = supabase_client()
    payload = {"status": status, "decided_at": "now()", **extra}
    sb.table("comments").update(payload).eq("platform", platform).eq("comment_id", comment_id).execute()


def set_commenter_classification(platform: str, account_url: str, classification: str, note: Optional[str] = None) -> None:
    """Whitelist / blacklist / unknown. Creates the row if missing."""
    sb = supabase_client()
    existing = fetch_commenter(platform, account_url)
    payload = {
        "platform": platform,
        "account_url": account_url,
        "classification": classification,
    }
    if note:
        payload["notes"] = note
    if not existing:
        sb.table("commenters").insert(payload).execute()
    else:
        sb.table("commenters").update(payload).eq("platform", platform).eq("account_url", account_url).execute()


def _pick_thanks_reply(display_name: Optional[str]) -> Optional[str]:
    """Inline canned-reply generator — mirrors engagement.classify.rules._pick_reply
    but inlined here to avoid a circular import (rules.py already imports from client.py)."""
    import json as _json
    import random as _random
    from pathlib import Path as _Path
    first = (display_name or "there").strip().split()[0] if display_name else "there"
    try:
        phrases_path = _Path(__file__).resolve().parent.parent / "classify" / "phrases.json"
        with open(phrases_path, "r", encoding="utf-8") as fp:
            templates = _json.load(fp).get("reply_templates", {}).get("like_and_thanks", [])
        return _random.choice(templates).format(first_name=first) if templates else f"Thanks {first}! 🙏"
    except Exception:
        return f"Thanks {first}! 🙏"


def cascade_blacklist_pending(platform: str, account_url: str) -> int:
    """When a commenter is blacklisted: reclassify their pending comments as
    ai/like_and_thanks AND auto-approve them with a canned reply pre-filled.
    User explicitly trusts blacklist → no need to click 'approve' on each."""
    sb = supabase_client()
    commenter = fetch_commenter(platform, account_url)
    reply = _pick_thanks_reply((commenter or {}).get("display_name"))
    res = (
        sb.table("comments")
        .update(
            {
                "classification": "ai",
                "verdict_source": "blacklist_cascade",
                "suggested_action": "like_and_thanks",
                "suggested_reply": reply,
                "confidence": 1.0,
                "status": "approved",
                "decided_at": "now()",
            }
        )
        .eq("platform", platform)
        .eq("commenter_url", account_url)
        .eq("status", "pending")
        .execute()
    )
    return len(res.data or [])


def migrate_fallback_ids_to_urn(platform: str, new_comments: list[dict]) -> int:
    """Promote legacy `fallback:<hash>` comment_ids to the new LinkedIn URN
    when the scraper now extracts one. Matches on (commenter_url, text-prefix)
    so the existing row keeps its classification + status + suggested_reply +
    any user action — the upsert that follows only touches scrape fields.
    Without this, a re-scrape would orphan classified rows and create
    duplicates keyed by URN.
    """
    sb = supabase_client()
    # Map: (commenter_url, text-prefix) → URN for everything new that has one.
    new_by_key: dict[tuple[str, str], str] = {}
    for c in new_comments:
        cid = c.get("comment_id") or ""
        if not cid.startswith("urn:li:comment:"):
            continue
        key = ((c.get("commenter_url") or ""), (c.get("text") or "")[:200])
        new_by_key.setdefault(key, cid)
    if not new_by_key:
        return 0

    fallback_rows = (
        sb.table("comments")
        .select("comment_id,commenter_url,text")
        .eq("platform", platform)
        .like("comment_id", "fallback:%")
        .execute()
        .data
        or []
    )

    migrated = 0
    for fb in fallback_rows:
        key = ((fb.get("commenter_url") or ""), (fb.get("text") or "")[:200])
        new_urn = new_by_key.get(key)
        if not new_urn or new_urn == fb["comment_id"]:
            continue
        try:
            sb.table("comments").update({"comment_id": new_urn}).eq(
                "platform", platform
            ).eq("comment_id", fb["comment_id"]).execute()
            migrated += 1
        except Exception as err:
            logger.warning("migrate %s → %s failed: %s", fb["comment_id"], new_urn, err)
    if migrated:
        logger.info("🔁 migrated %d fallback ids → URN", migrated)
    return migrated


def mark_replied_as_ignored(platform: str = "linkedin") -> int:
    """Comments where I've already replied don't need to be in the triage inbox.
    Flip pending → ignored. Only touches rows the user hasn't explicitly acted on
    (status='pending'); approved/rejected/etc. survive."""
    sb = supabase_client()
    res = (
        sb.table("comments")
        .update({"status": "ignored", "decided_at": "now()"})
        .eq("platform", platform)
        .eq("status", "pending")
        .not_.is_("my_reply_text", "null")
        .execute()
    )
    return len(res.data or [])


def fetch_labeled_training_set(platform: str) -> list[dict]:
    """Return every comment whose commenter has an explicit whitelist/blacklist
    label, with the label attached as `_label` (1=ai, 0=human). Used by
    `engagement.classify.local_model.train()`.

    Done in two queries because the `comments` ↔ `commenters` relationship is
    not modeled as a PostgREST foreign key (composite PK + denormalised text
    URLs), so embedded selects aren't available.
    """
    sb = supabase_client()
    labeled = (
        sb.table("commenters")
        .select("account_url,classification")
        .eq("platform", platform)
        .in_("classification", ["whitelist", "blacklist"])
        .execute()
        .data
        or []
    )
    if not labeled:
        return []
    label_for = {row["account_url"]: row["classification"] for row in labeled}
    rows = (
        sb.table("comments")
        .select("*")
        .eq("platform", platform)
        .in_("commenter_url", list(label_for.keys()))
        .execute()
        .data
        or []
    )
    out: list[dict] = []
    for r in rows:
        cls = label_for.get(r.get("commenter_url"))
        if cls == "blacklist":
            r["_label"] = 1
        elif cls == "whitelist":
            r["_label"] = 0
        else:
            continue
        out.append(r)
    return out


def cascade_whitelist_pending(platform: str, account_url: str) -> int:
    sb = supabase_client()
    res = (
        sb.table("comments")
        .update(
            {
                "classification": "human",
                "verdict_source": "whitelist_cascade",
                "suggested_action": "surface_to_me",
                "confidence": 1.0,
            }
        )
        .eq("platform", platform)
        .eq("commenter_url", account_url)
        .eq("status", "pending")
        .execute()
    )
    return len(res.data or [])


# ---------- Notion ----------

def notion_client():
    from notion_client import Client

    cfg = load_config()
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing notion.api_token in config.json")
    return Client(auth=token)


__all__ = [
    "load_config",
    "load_engagement_config",
    "supabase_client",
    "notion_client",
    "upsert_commenters",
    "upsert_comments",
    "fetch_pending_comments",
    "fetch_comments_by_status",
    "fetch_commenter",
    "fetch_commenters_by_urls",
    "update_comment_status",
    "set_commenter_classification",
    "cascade_blacklist_pending",
    "cascade_whitelist_pending",
    "mark_replied_as_ignored",
    "migrate_fallback_ids_to_urn",
    "fetch_labeled_training_set",
]
