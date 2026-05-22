# Engagement triage — UI cleanup pass

**Date:** 2026-05-22
**Branch:** feat/engagement-triage

## What was done

A cleanup pass over the engagement review UI based on hands-on feedback.

### Comment permalink fix
`_comment_link` now emits LinkedIn's `?dashCommentUrn=urn:li:fsd_comment:(<comment-num>,<post-urn>)`
instead of the ignored `?commentUrn=urn:li:comment:(...)`. The two URN halves are
the same — reversed in order and re-wrapped as `fsd_comment` — which is the form
LinkedIn's "copy link to comment" produces and the only one that actually scrolls
to the comment. The stored `comment_id` is unchanged (still the canonical
`urn:li:comment:(...)` primary key); the transform happens only at link-render time,
so no Supabase migration was needed.

### Filters + counts on one line
`render_sidebar_metrics()` gained a `horizontal` keyword — the engagement-tab
expander now lays pending / approved / sent / rejected+ignored across a single
row. The standalone `review_app.py` sidebar keeps the default vertical layout.

### Compact comment cards
Card header collapsed from a 3-column stacked block into a single inline row
(name · verdict · commenter whitelist/blacklist badge · profile/comment links ·
status). Action buttons now sit on one tight row sized to the exact button count
(previously allocated 6 columns and skipped index 3, leaving a visible gap).

### pending / all view filter
`render_real_tab` and `render_ai_tab` gained a `pending / all` radio (default
`pending`). `all` loads every status so comments already marked read / AI /
approved stay findable. Per-commenter review remains served by the commenters tab.

### Commenter-class badge
Each card now shows the commenter's whitelist/blacklist/unknown classification,
making the surface / whitelist / blacklist routing legible at a glance. Auto-routing
itself was already correct (blacklist → auto-approved, whitelist → "real comments")
— no logic change.

## Files modified

- `engagement/ui.py`
- `app/tab_engagement.py`

## Not code bugs (no change)

- *"Invalid HTTP request received"* — the app's server logging non-HTTP
  connections on its port (stale `https://` browser tab or another process
  probing it).
- *`supabase service_role_key failed: Invalid API key`* — the `service_role_key`
  in `config/config.json` is invalid/expired; the engagement client falls back to
  the anon/`key` so the app keeps working. Paste a valid service-role key to
  silence it.

## Validation

- `py_compile` on both edited files — OK.
- Streamlit boot check (`streamlit run app/app.py --server.headless true`) —
  HTTP 200.
