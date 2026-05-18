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
    """Build a permalink that opens the post AND scrolls to this specific
    comment. Works when comment_id is a real LinkedIn URN; falls back to the
    plain post URL for legacy fallback:<hash> rows from before URN extraction."""
    if not post_url:
        return ""
    if not isinstance(comment_id, str) or not comment_id.startswith("urn:li:comment:"):
        return post_url
    sep = "&" if "?" in post_url else "?"
    return f"{post_url}{sep}commentUrn={quote(comment_id, safe='')}"


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

def _render_comment_card(row: dict, *, tab: str) -> None:
    cid = row["comment_id"]
    platform = row["platform"]
    commenter_url = _s(row.get("commenter_url"))
    display_name = _s(row.get("display_name"), "(unknown)")
    text = _s(row.get("text"))
    post_url = _s(row.get("post_url"))
    suggested_reply = _s(row.get("suggested_reply"))
    classification = _s(row.get("classification"), "unknown")
    confidence = row.get("confidence")
    reasons = _list(row.get("verdict_reasons"))

    badge = {"human": "🧑 human", "ai": "🤖 ai", "unknown": "❓ unknown"}.get(classification, classification)
    conf_str = f" · {confidence:.2f}" if isinstance(confidence, (int, float)) else ""

    with st.container(border=True):
        head_cols = st.columns([3, 2, 2])
        with head_cols[0]:
            st.markdown(f"**{display_name}**  \n{badge}{conf_str}")
            if commenter_url:
                st.markdown(f"[open profile ↗]({commenter_url})")
        with head_cols[1]:
            link = _comment_link(post_url, cid)
            if link:
                label = "open comment ↗" if link != post_url else "open post ↗"
                st.markdown(f"on post: [{label}]({link})")
            if row.get("posted_at"):
                st.caption(f"commented {row['posted_at']}")
        with head_cols[2]:
            if reasons:
                rules_fired = ", ".join(sorted({r.get("rule", "?") for r in reasons}))
                st.caption(f"rules: {rules_fired}")

        st.write(text or "_(no text extracted)_")

        my_reply = _s(row.get("my_reply_text"))
        if my_reply:
            when_str = _s(row.get("my_replied_at"))
            with st.container(border=True):
                st.caption(f"✅ you already replied {when_str}".rstrip())
                st.write(my_reply)

        if suggested_reply:
            st.code(suggested_reply, language=None)

        btn_cols = st.columns(6)
        key_base = f"{tab}-{platform}-{cid}"
        if tab == "ai":
            btn_cols[0].button("✅ approve", key=f"{key_base}-approve", width="stretch",
                               on_click=_act_approve, args=(platform, cid))
            btn_cols[1].button("🚫 reject", key=f"{key_base}-reject", width="stretch",
                               on_click=_act_reject, args=(platform, cid))
            btn_cols[2].button("🧑 surface", key=f"{key_base}-surface", width="stretch",
                               on_click=_act_surface, args=(platform, cid))
        else:
            btn_cols[0].button("✓ mark read", key=f"{key_base}-ignore", width="stretch",
                               on_click=_act_ignore, args=(platform, cid))
            btn_cols[1].button("🤖 mark AI", key=f"{key_base}-reject", width="stretch",
                               on_click=_act_reject, args=(platform, cid))
        btn_cols[4].button("⬇️ whitelist commenter", key=f"{key_base}-wl", width="stretch",
                           on_click=_act_whitelist, args=(platform, commenter_url), disabled=not commenter_url)
        btn_cols[5].button("⛔ blacklist commenter", key=f"{key_base}-bl", width="stretch",
                           on_click=_act_blacklist, args=(platform, commenter_url), disabled=not commenter_url)


# ---------- Sidebar + tab renderers ----------

def render_sidebar_metrics() -> None:
    """Engagement-specific sidebar block. Callers wrap with `with st.sidebar:`."""
    counts = _load_classification_counts()
    st.metric("pending", counts.get("pending", 0))
    st.metric("approved", counts.get("approved", 0))
    st.metric("sent", counts.get("sent", 0))
    st.metric("rejected + ignored", counts.get("rejected", 0) + counts.get("ignored", 0))


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


def render_real_tab(platform: str | None, search: str) -> None:
    df = _load_comments(("pending",), platform)
    if not df.empty:
        df = df[df["classification"] == "human"]
        if search:
            df = df[
                df["text"].fillna("").str.lower().str.contains(search)
                | df["display_name"].fillna("").str.lower().str.contains(search)
            ]
    if df.empty:
        st.info("inbox clear — no real comments waiting.")
        return
    st.caption(f"{len(df)} real comment(s) awaiting your personal reply")
    for _, row in df.iterrows():
        _render_comment_card(row.to_dict(), tab="real")


def render_ai_tab(platform: str | None, search: str) -> None:
    df = _load_comments(("pending",), platform)
    if not df.empty:
        df = df[df["classification"].isin(["ai", "unknown"])]
        if search:
            df = df[
                df["text"].fillna("").str.lower().str.contains(search)
                | df["display_name"].fillna("").str.lower().str.contains(search)
            ]
        df = df.sort_values(["classification", "confidence"], ascending=[True, False])
    if df.empty:
        st.info("triage clear — no AI-flagged comments waiting.")
        return
    st.caption(f"{len(df)} comment(s) to triage")
    for _, row in df.iterrows():
        _render_comment_card(row.to_dict(), tab="ai")


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
