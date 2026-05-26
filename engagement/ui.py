"""Reusable Streamlit render functions for the engagement review UI.

Imported by both the standalone `engagement/review_app.py` entrypoint AND the
control-panel's `app/tab_engagement.py` — so a UX change here lands in both
places. No top-level rendering and no `st.set_page_config()` (the caller owns
those).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from engagement.db.client import (  # noqa: E402
    cascade_blacklist_pending,
    cascade_whitelist_pending,
    set_commenter_classification,
    supabase_client,
    update_comment_status,
)

CLASS_BADGE = {
    "whitelist": "🟢 whitelist",
    "blacklist": "⛔ blacklist",
    "unknown":   "❓ unknown",
}


# pandas turns SQL nulls into float('nan') when loading via supabase-py → DataFrame.
# `NaN or default` short-circuits to NaN (NaN is truthy), so the old `x or ""`
# idiom rendered literal "nan" or, for list columns, blew up with TypeError on
# iteration. These helpers normalise — use them on every DataFrame.cell read.
def _s(val, default: str = "") -> str:
    return val if isinstance(val, str) and val else default


def _list(val) -> list:
    return val if isinstance(val, list) else []


def _comment_link(post_url: str, comment_id: str) -> str:
    """Build a permalink that opens the post AND scrolls to this specific comment.

    The stored comment_id is `urn:li:comment:(<post-urn>,<comment-num>)`. LinkedIn
    ignores `?commentUrn=` for that form — the query param it actually honours is
    `?dashCommentUrn=urn:li:fsd_comment:(<comment-num>,<post-urn>)`: the same two
    halves, reversed, re-wrapped as `fsd_comment`. Falls back to the plain post URL
    for legacy fallback:<hash> rows or any URN we can't parse."""
    if not post_url:
        return ""
    prefix = "urn:li:comment:("
    if not isinstance(comment_id, str) or not comment_id.startswith(prefix):
        return post_url
    inner = comment_id[len(prefix):]
    if not inner.endswith(")") or "," not in inner:
        return post_url
    post_urn, comment_num = inner[:-1].rsplit(",", 1)
    dash_urn = f"urn:li:fsd_comment:({comment_num},{post_urn})"
    sep = "&" if "?" in post_url else "?"
    return f"{post_url}{sep}dashCommentUrn={quote(dash_urn, safe='')}"


# ---------- Data layer (cached) ----------

@st.cache_resource
def _client():
    return supabase_client()


@st.cache_data(ttl=30)
def _load_comments(status_in: tuple[str, ...], platform: str | None) -> pd.DataFrame:
    sb = _client()
    q = sb.table("comments").select("*").in_("status", list(status_in)).order("scraped_at", desc=True).limit(1000)
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=30)
def _load_classification_counts() -> dict:
    sb = _client()
    out: dict = {}
    for status in ("pending", "approved", "sent", "rejected", "ignored"):
        res = sb.table("comments").select("comment_id", count="exact").eq("status", status).limit(1).execute()
        out[status] = res.count or 0
    return out


@st.cache_data(ttl=30)
def _load_all_comments(platform: str | None) -> pd.DataFrame:
    sb = _client()
    q = sb.table("comments").select("*").order("posted_at", desc=True).limit(5000)
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=30)
def _load_all_commenters(platform: str | None) -> pd.DataFrame:
    sb = _client()
    q = sb.table("commenters").select("*").order("last_seen", desc=True).limit(5000)
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def invalidate_caches() -> None:
    _load_comments.clear()
    _load_classification_counts.clear()
    _load_all_comments.clear()
    _load_all_commenters.clear()


# ---------- Action handlers ----------

def _act_approve(platform: str, comment_id: str) -> None:
    update_comment_status(platform, comment_id, "approved")
    invalidate_caches()


def _act_reject(platform: str, comment_id: str) -> None:
    update_comment_status(platform, comment_id, "rejected")
    invalidate_caches()


def _act_ignore(platform: str, comment_id: str) -> None:
    update_comment_status(platform, comment_id, "ignored")
    invalidate_caches()


def _act_surface(platform: str, comment_id: str) -> None:
    update_comment_status(
        platform, comment_id, "pending",
        classification="human", suggested_action="surface_to_me", suggested_reply=None,
    )
    invalidate_caches()


def _act_blacklist(platform: str, account_url: str) -> None:
    set_commenter_classification(platform, account_url, "blacklist")
    cascade_blacklist_pending(platform, account_url)
    invalidate_caches()


def _act_whitelist(platform: str, account_url: str) -> None:
    set_commenter_classification(platform, account_url, "whitelist")
    cascade_whitelist_pending(platform, account_url)
    invalidate_caches()


# ---------- Card renderer (shared by real + ai tabs) ----------

def _render_comment_card(row: dict, *, tab: str, commenter_cls: dict[str, str] | None = None) -> None:
    cid = row["comment_id"]
    platform = row["platform"]
    commenter_url = _s(row.get("commenter_url"))
    display_name = _s(row.get("display_name"), "(unknown)")
    text = _s(row.get("text"))
    post_url = _s(row.get("post_url"))
    suggested_reply = _s(row.get("suggested_reply"))
    classification = _s(row.get("classification"), "unknown")
    status = _s(row.get("status"))
    confidence = row.get("confidence")
    reasons = _list(row.get("verdict_reasons"))

    badge = {"human": "🧑 human", "ai": "🤖 ai", "unknown": "❓ unknown"}.get(classification, classification)
    conf_str = f" · {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
    cls = (commenter_cls or {}).get(commenter_url, "unknown")
    commenter_badge = CLASS_BADGE.get(cls, "")

    with st.container(border=True):
        # Single-line header: name · verdict · commenter class · links · status.
        link = _comment_link(post_url, cid)
        head = [f"**{display_name}**", f"{badge}{conf_str}"]
        if commenter_badge:
            head.append(commenter_badge)
        if commenter_url:
            head.append(f"[profile ↗]({commenter_url})")
        if link:
            head.append(f"[{'comment ↗' if link != post_url else 'post ↗'}]({link})")
        if status and status != "pending":
            head.append(f"`{status}`")
        st.markdown(" · ".join(head))

        meta = []
        if row.get("posted_at"):
            meta.append(f"commented {row['posted_at']}")
        if reasons:
            meta.append("rules: " + ", ".join(sorted({r.get("rule", "?") for r in reasons})))
        if meta:
            st.caption(" · ".join(meta))

        st.write(text or "_(no text extracted)_")

        my_reply = _s(row.get("my_reply_text"))
        if my_reply:
            when_str = _s(row.get("my_replied_at"))
            with st.container(border=True):
                st.caption(f"✅ you already replied {when_str}".rstrip())
                st.write(my_reply)

        if suggested_reply:
            st.code(suggested_reply, language=None)

        # Build the action list, then give it exactly that many columns so the
        # buttons sit on one tight row with no empty gap.
        key_base = f"{tab}-{platform}-{cid}"
        actions: list[tuple] = []
        if tab == "ai":
            actions.append(("✅ approve", f"{key_base}-approve", _act_approve, (platform, cid), False))
            actions.append(("🚫 reject", f"{key_base}-reject", _act_reject, (platform, cid), False))
            actions.append(("🧑 surface", f"{key_base}-surface", _act_surface, (platform, cid), False))
        else:
            actions.append(("✓ mark read", f"{key_base}-ignore", _act_ignore, (platform, cid), False))
            actions.append(("🤖 mark AI", f"{key_base}-reject", _act_reject, (platform, cid), False))
        actions.append(("⬇️ whitelist", f"{key_base}-wl", _act_whitelist, (platform, commenter_url), not commenter_url))
        actions.append(("⛔ blacklist", f"{key_base}-bl", _act_blacklist, (platform, commenter_url), not commenter_url))

        for col, (label, key, handler, args, disabled) in zip(st.columns(len(actions)), actions):
            col.button(label, key=key, width="stretch", on_click=handler, args=args, disabled=disabled)


# ---------- Sidebar + tab renderers ----------

def render_sidebar_metrics(*, horizontal: bool = False) -> None:
    """Engagement-specific metrics block. `horizontal=True` lays the 4 counts
    across one row (for the engagement-tab expander); the default vertical layout
    suits the standalone review_app sidebar. Callers wrap with `with st.sidebar:`."""
    counts = _load_classification_counts()
    items = [
        ("pending", counts.get("pending", 0)),
        ("approved", counts.get("approved", 0)),
        ("sent", counts.get("sent", 0)),
        ("rejected + ignored", counts.get("rejected", 0) + counts.get("ignored", 0)),
    ]
    if horizontal:
        for col, (label, val) in zip(st.columns(len(items)), items):
            col.metric(label, val)
    else:
        for label, val in items:
            st.metric(label, val)


def render_sidebar_filters(*, key_suffix: str = "") -> tuple[str | None, str]:
    """Returns (platform, search_text)."""
    platform = st.selectbox(
        "platform",
        options=["linkedin", None],
        format_func=lambda v: v or "(all)",
        key=f"engagement-filter-platform{key_suffix}",
    )
    search = st.text_input("search text / name", key=f"engagement-filter-search{key_suffix}").strip().lower()
    if st.button("🔄 refresh data", key=f"engagement-btn-refresh{key_suffix}", width="stretch"):
        invalidate_caches()
    return platform, search


_ALL_STATUSES = ("pending", "approved", "sent", "rejected", "ignored")


# Per-tab user-manual legends explaining what each action button does and the
# side-effects (status changes / cascade behaviour). Rendered just below the
# tabs, above the show filter, so the meaning of the row-level buttons is
# always one glance away.
_REAL_LEGEND = """**What each action does:**

- **✓ mark read** — You've read this comment and don't need to reply. Status moves to `ignored`; the row disappears from "show pending" but stays in "show all".
- **🤖 mark AI** — Wrong verdict, this is actually AI noise. Status moves to `rejected`; the row leaves this tab.
- **⬇️ whitelist** — Trust this commenter. All their pending comments get reclassified as human and stay surfaced here; future comments by them bypass the AI check.
- **⛔ blacklist** — Distrust this commenter. All their pending comments are auto-approved with the canned "thanks @first_name" reply (ready to copy-paste in the AI triage tab); future comments by them get the same treatment automatically."""

_AI_LEGEND = """**What each action does:**

- **✅ approve** — The canned reply is good. Status moves to `approved`; copy the reply from the code block above the buttons and paste it into LinkedIn's native composer yourself.
- **🚫 reject** — Don't leave any reply. Status moves to `rejected`; the row leaves this tab.
- **🧑 surface** — Wrong verdict, this is actually a real human comment. Reclassifies as human and moves it to the real-comments tab for your personal reply.
- **⬇️ whitelist** — Trust this commenter. Same effect as in the real-comments tab: pending comments are reclassified as human and surfaced there; future comments bypass the AI check.
- **⛔ blacklist** — Distrust this commenter. Their pending comments are auto-approved with the canned thanks reply; future ones the same."""


def _render_legend(body: str) -> None:
    """Per-tab user-manual block. Wrapped in an expander so the cards aren't
    pushed off-screen on small viewports."""
    with st.expander("ℹ️ what each action does", expanded=False):
        st.markdown(body)


_COMMENTERS_EXPLAINER = """\
### What this tab is for

A per-commenter pivot view of everyone who's commented on your posts. The point is to **spot patterns over time** that you can't see one comment at a time: who comments daily, who only fires within minutes of every new post, who comments dozens of times but never publishes their own content.

These patterns drive the whitelist / blacklist labels that feed back into both classifier layers:

- **Whitelist hits** short-circuit the classifier — all their pending + future comments stay flagged human.
- **Blacklist hits** also short-circuit — pending comments get auto-approved with the canned thanks reply.
- **Both labels** are the training set for the local sklearn model (Phase 2a). Every whitelist commenter contributes their comments as negative (human) examples; every blacklist commenter contributes positives (AI). Retrain after marking a batch.

---

### How to read the table

- **Rows** = commenters. The filter chips at the top scope by `classification` (whitelist / blacklist / unknown).
- **Columns** = `commenter` · `class` · `total` · then one column per day for the last N days (newest left, dd/mm format).
- **Totals** are across *all* time, not just the visible day window — so a commenter who hammered you a month ago still sticks out.
- **Sort** = by total desc, so the heaviest commenters come first.
- The `days` selector (top right) just changes the daily-column window; it doesn't filter the rows.

**Click a row** to drill into that commenter's individual comments, sorted newest first, with your already-posted replies shown inline (when present). Click again on the same row to deselect.

---

### Signals worth eyeballing

- **Heavy total + no comments-per-day variance** — likely a template-driven bot. The same person who comments on *every* one of your posts within minutes is rarely engaging organically.
- **Sub-2-minute timestamps repeated across posts** — the rules classifier already catches single-comment cases, but seeing the pattern across many posts is the real signal.
- **Identical text across multiple commenters** — open both rows; if the texts match verbatim, you've found a coordinated network. Blacklist all of them.
- **Whitelist drift** — a commenter you whitelisted six months ago who now only leaves generic praise. Demoting (set classification back to unknown via the action buttons in the per-comment cards) un-trusts them without going as far as blacklist.

---

### Technical details

- Data comes from a single Supabase select per table: `comments` (limit 5000, ordered by `posted_at desc`) and `commenters` (limit 5000, ordered by `last_seen desc`). Both are cached for 30 seconds in Streamlit (`@st.cache_data(ttl=30)`); the **🔄 refresh data** button in the engagement filters expander busts the cache.
- The pivot is built in-memory with pandas — no SQL pivot, no Postgres-side window functions. `_build_commenter_pivot` does `pivot_table(index='commenter_url', columns='day', values='comment_id', aggfunc='count')` then joins on `commenters` for the `classification` + `display_name` columns. Missing days get zero-filled so the day columns are stable.
- Selection state is managed by `st.dataframe(on_select='rerun', selection_mode='single-row')` — Streamlit's native row-pick API, no callback ceremony.
- The drill-down rebuilds the comment list from the same in-memory DataFrame, so it doesn't re-query Supabase when you click. Switching tabs *does* trigger a fresh load (within the 30s TTL).
"""


def _view_filter(key: str) -> tuple[str, ...]:
    """Render the `pending / all` view radio; return the status tuple to load."""
    view = st.radio(
        "show", ["pending", "all"], horizontal=True, key=key,
        help="'all' also shows comments you've already marked read / AI / approved.",
    )
    return ("pending",) if view == "pending" else _ALL_STATUSES


def _commenter_class_map(platform: str | None) -> dict[str, str]:
    cdf = _load_all_commenters(platform)
    if cdf.empty:
        return {}
    return dict(zip(cdf["account_url"], cdf["classification"]))


def render_real_tab(platform: str | None, search: str) -> None:
    _render_legend(_REAL_LEGEND)
    statuses = _view_filter("real-view-filter")
    df = _load_comments(statuses, platform)
    if not df.empty:
        df = df[df["classification"] == "human"]
        if search:
            df = df[
                df["text"].fillna("").str.lower().str.contains(search)
                | df["display_name"].fillna("").str.lower().str.contains(search)
            ]
        df = df.sort_values("posted_at", ascending=False, na_position="last")
    if df.empty:
        st.info("inbox clear — no real comments match the current view.")
        return
    cmap = _commenter_class_map(platform)
    st.caption(f"{len(df)} real comment(s) — {statuses[0] if len(statuses) == 1 else 'all statuses'}")
    for _, row in df.iterrows():
        _render_comment_card(row.to_dict(), tab="real", commenter_cls=cmap)


def render_ai_tab(platform: str | None, search: str) -> None:
    _render_legend(_AI_LEGEND)
    statuses = _view_filter("ai-view-filter")
    df = _load_comments(statuses, platform)
    if not df.empty:
        df = df[df["classification"].isin(["ai", "unknown"])]
        if search:
            df = df[
                df["text"].fillna("").str.lower().str.contains(search)
                | df["display_name"].fillna("").str.lower().str.contains(search)
            ]
        df = df.sort_values("posted_at", ascending=False, na_position="last")
    if df.empty:
        st.info("triage clear — no AI-flagged comments match the current view.")
        return
    cmap = _commenter_class_map(platform)
    st.caption(f"{len(df)} comment(s) — {statuses[0] if len(statuses) == 1 else 'all statuses'}")
    for _, row in df.iterrows():
        _render_comment_card(row.to_dict(), tab="ai", commenter_cls=cmap)


# ---------- Commenters analysis ----------

def _build_commenter_pivot(comments_df: pd.DataFrame, commenters_df: pd.DataFrame, days: int) -> pd.DataFrame:
    """Rows = commenter (with classification), cols = total + last `days` dd/mm columns (newest left)."""
    if comments_df.empty:
        return pd.DataFrame()

    today = datetime.now(timezone.utc).date()
    day_columns = [(today - timedelta(days=i)) for i in range(days)]
    day_labels = [d.strftime("%d/%m") for d in day_columns]

    cdf = comments_df.copy()
    cdf["posted_at"] = pd.to_datetime(cdf["posted_at"], errors="coerce", utc=True)
    cdf["day"] = cdf["posted_at"].dt.date

    in_window = cdf[cdf["day"].isin(day_columns)]
    pivot = in_window.pivot_table(
        index="commenter_url", columns="day", values="comment_id", aggfunc="count", fill_value=0,
    )
    for d in day_columns:
        if d not in pivot.columns:
            pivot[d] = 0
    pivot = pivot[day_columns]
    pivot.columns = day_labels

    totals = cdf.groupby("commenter_url")["comment_id"].count().rename("total")

    if not commenters_df.empty:
        meta = commenters_df.set_index("account_url")[["display_name", "classification"]]
    else:
        meta = (
            cdf.dropna(subset=["display_name"])
            .drop_duplicates("commenter_url")
            .set_index("commenter_url")[["display_name"]]
            .assign(classification="unknown")
        )

    out = (
        pd.concat([totals, pivot], axis=1)
        .join(meta, how="left")
        .reset_index()
        .rename(columns={"commenter_url": "account_url"})
    )
    out["display_name"] = out["display_name"].fillna("(unknown)")
    out["classification"] = out["classification"].fillna("unknown")
    out["total"] = out["total"].fillna(0).astype(int)
    for c in day_labels:
        out[c] = out[c].fillna(0).astype(int)

    cols = ["display_name", "classification", "total", *day_labels, "account_url"]
    return out[cols].sort_values("total", ascending=False).reset_index(drop=True)


def _render_drill_down(account_url: str, comments_df: pd.DataFrame, platform: str | None) -> None:
    rows = comments_df[comments_df["commenter_url"] == account_url].sort_values("posted_at", ascending=False)
    if rows.empty:
        st.info("no comments stored for this commenter")
        return
    name = next((n for n in rows["display_name"] if isinstance(n, str) and n), "(unknown)")
    commenter_cls = "unknown"
    cdf = _load_all_commenters(platform)
    if not cdf.empty:
        match = cdf[cdf["account_url"] == account_url]
        if not match.empty:
            commenter_cls = _s(match.iloc[0].get("classification"), "unknown")
    badge = CLASS_BADGE.get(commenter_cls, commenter_cls)
    st.subheader(f"selected: {name}")
    st.markdown(f"{badge} · {len(rows)} comment(s) · [open profile ↗]({account_url})")

    for _, r in rows.iterrows():
        with st.container(border=True):
            posted = r.get("posted_at")
            posted_str = ""
            if isinstance(posted, str):
                posted_str = posted[:10]
            elif hasattr(posted, "strftime"):
                posted_str = posted.strftime("%Y-%m-%d")
            post_url = _s(r.get("post_url"))
            comment_id = _s(r.get("comment_id"))
            link = _comment_link(post_url, comment_id)
            head = f"▸ **{posted_str}**" if posted_str else "▸"
            if link:
                label = "open comment ↗" if link != post_url else "open post ↗"
                head += f"  ·  [{label}]({link})"
            st.markdown(head)
            st.write(_s(r.get("text"), "_(no text)_"))

            my_reply = _s(r.get("my_reply_text"))
            if my_reply:
                st.caption("you replied:")
                st.write(my_reply)


def render_commenters_tab(platform: str | None, search: str) -> None:
    with st.expander("📚 how to use this tab", expanded=False):
        st.markdown(_COMMENTERS_EXPLAINER)

    comments_df = _load_all_comments(platform)
    commenters_df = _load_all_commenters(platform)

    if comments_df.empty:
        st.info("no comments scraped yet — run the Scrape action in the Engagement tab.")
        return

    filt_cols = st.columns([3, 1])
    with filt_cols[0]:
        class_filter = st.multiselect(
            "classification filter",
            options=["whitelist", "blacklist", "unknown"],
            default=["whitelist", "blacklist", "unknown"],
            key="people-class-filter",
        )
    with filt_cols[1]:
        days = st.selectbox("days", options=[7, 14, 30, 60], index=1, key="people-days")

    pivot_df = _build_commenter_pivot(comments_df, commenters_df, days=days)
    if not pivot_df.empty:
        pivot_df = pivot_df[pivot_df["classification"].isin(class_filter)]
    if search:
        pivot_df = pivot_df[pivot_df["display_name"].fillna("").str.lower().str.contains(search)]

    if pivot_df.empty:
        st.info("no commenters match the current filter.")
        return

    st.caption(f"{len(pivot_df)} commenter(s) · sorted by total desc")
    display_df = pivot_df.drop(columns=["account_url"]).rename(
        columns={"display_name": "commenter", "classification": "class"}
    )
    selection = st.dataframe(
        display_df, width="stretch", hide_index=True,
        on_select="rerun", selection_mode="single-row", key="people-table",
    )
    selected_rows = (selection.selection or {}).get("rows", []) if hasattr(selection, "selection") else []
    if selected_rows:
        idx = selected_rows[0]
        if 0 <= idx < len(pivot_df):
            st.divider()
            _render_drill_down(pivot_df.iloc[idx]["account_url"], comments_df, platform)
    else:
        st.caption("👆 click a row to drill into that commenter's comments + your replies.")


__all__ = [
    "render_sidebar_metrics",
    "render_sidebar_filters",
    "render_real_tab",
    "render_ai_tab",
    "render_commenters_tab",
    "invalidate_caches",
]
