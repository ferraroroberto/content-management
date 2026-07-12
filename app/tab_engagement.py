"""Engagement tab — scrape / classify run controls + the existing review UI."""

from __future__ import annotations

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_combined_log_panel,
    render_combined_status_badge,
    start_pipeline,
)
from engagement.ui import (
    invalidate_caches,
    render_ai_tab,
    render_commenters_tab,
    render_real_tab,
    render_sidebar_filters,
    render_sidebar_metrics,
)

SCRAPE_NAME = "engagement-scrape"
CLASSIFY_NAME = "engagement-classify"


_HOW_IT_WORKS = """\
### What this pipeline does

Reads comments on your own LinkedIn posts, classifies each as **human / AI / unknown**, and stages canned acknowledgements for the AI ones so you can batch them through LinkedIn's native composer with one copy-paste per reply.

The pipeline **never auto-sends**. Every reply is a manual copy-paste — by design, because automating reply submission via the web UI violates LinkedIn's User Agreement §8.2 (see [issue #20](https://github.com/ferraroroberto/content-management/issues/20)).

---

### Stage 1 — scrape

**Source.** The Notion editorial database (the same one the planning pipeline writes into). Rows where `link LI` is set and `date >= today - (days-1)` are the inputs. `days` is the input above; `days=5` means today plus the four prior days.

**Browser session.** Each post URL is opened in the **`planning/linkedin/chrome_user_data`** profile — a persistent Chrome session that's already logged into your LinkedIn account. Reused so we don't need a separate engagement bootstrap. Headful by default for first runs (so you can watch selector iteration); headless is opt-in once selectors are stable.

**Selectors.** LinkedIn obfuscates CSS class names and shuffles them every deploy, so the scraper hooks the *stable* parts:

- comment list container — `[data-testid$='FeedType_FEED_DETAIL']`
- comment text — `[data-testid='expandable-text-box']`
- profile links — `<a href="https://www.linkedin.com/in/<handle>/">`

Sort is switched to **Most recent** before extraction so we don't miss comments below LinkedIn's relevance threshold. "Show more replies" buttons are clicked until exhausted (capped at `expand_max_clicks` in config).

**Per-comment fields.** `commenter_url`, `display_name`, `text`, `comment_id` (LinkedIn URN), `posted_at` (reconstructed from LinkedIn's relative `2h` / `5m` / `1d` timestamps by subtracting the offset from scrape time — accurate to ~1 display unit, fine for cadence rules).

**Output.** Upserted into Supabase `comments` and `commenters` tables. Keys are `(platform, comment_id)` and `(platform, account_url)` respectively, so re-scraping the same posts is a no-op (idempotent).

---

### Stage 2 — classify

Layered classifier — earlier layers are cheaper and more auditable; later layers handle the ambiguous middle.

**Layer 1 — rules** (Phase 1, shipped). Pure-Python pipeline driven by `engagement/classify/phrases.json`. Per comment, a score is summed from:

- generic-praise phrase hits (configurable list of ~45 substrings: "great post", "thanks for sharing", "💯", …)
- short comment (≤60 chars)
- no personal token (no `you / your / i / me / my`)
- emoji-only
- exact-text duplicate across multiple commenters (template hammer)
- sub-2-minute reply cadence (commented within 2 min of the post)

Score ≥ `ai_classification_threshold` (default 0.50) → **AI**. Below → **unknown**. Whitelist/blacklist commenters short-circuit the score: their pending comments are reclassified immediately (whitelist → human; blacklist → ai + canned reply pre-filled + status auto-promoted to `approved`).

**Layer 2 — local sklearn model** (Phase 2a, this branch). Logistic regression on `TfidfVectorizer(ngram_range=(1,2))` + `TfidfVectorizer(analyzer='char_wb', ngram_range=(3,5))` + 6 per-comment scalar features (the same ones the rules layer derives). Trained on your accumulated commenter-level whitelist/blacklist labels — every comment by a blacklisted commenter is a positive AI example, every comment by a whitelisted commenter is a negative.

- Runs **only on rows the rules layer left as `unknown`** (layered, not blended — so each verdict carries its provenance).
- Threshold to upgrade to AI: `local_model_ai_threshold` (default 0.70). Conservative so canned replies aren't wrongly staged.
- Requires `local_model_min_train_per_class` (default 20) of each class before training is allowed.
- Persisted as `engagement/classify/local_model.joblib` (gitignored — user-specific artifact). If the file isn't on disk yet, the classify pass is lossless: behaviour is identical to rules-only.
- The featurizer (`local_model.featurize_one`) re-imports the per-comment helpers from `rules.py`, so train-time and inference-time signals **can't drift**. Tune a rule → retrain the model.

**Layer 3 — LLM fallback** (Phase 3, future). Structured-output call to a small LLM (Gemini Flash-Lite or Haiku 4.5) for the ~10% genuine ambiguous middle, cached by `(commenter_url, comment_hash)`. Not implemented yet.

**Verdict written back** to the `comments` row: `classification` (human/ai/unknown), `confidence` (0-1), `verdict_source` (`rules` / `local` / `whitelist` / `blacklist` / `whitelist_cascade` / `blacklist_cascade`), `verdict_reasons` (jsonb: which rule + weight + probability fired), `suggested_action` (`surface_to_me` / `like_and_thanks` / `ignore`), `suggested_reply` (the canned thanks template for AI rows).

---

### Storage

| table | PK | role |
|---|---|---|
| `comments` | `(platform, comment_id)` | one row per scraped comment; carries verdict, status, suggested reply, your already-posted reply (if any) |
| `commenters` | `(platform, account_url)` | one row per author; carries `classification` (whitelist/blacklist/unknown), `signals` jsonb (future per-account features), free-text `notes` |

Indexes: `(status, classification)` and `(platform, commenter_url)` so the review-UI's filtered queries stay fast.

---

### Why this shape

- **Layered, not blended.** Each layer's verdict carries its provenance — you can always tell which rule (or which model) classified a given comment. Makes `phrases.json` tuning and false-positive triage debuggable.
- **Bias toward surfacing.** Below-threshold rows stay `unknown` and appear in the real-comments tab. Over-surfacing wastes a glance; wrongly staging a canned reply on a real human comment is worse.
- **No auto-send, ever.** Approved rows wait in Supabase for your manual copy-paste. This is the load-bearing TOS-compliance decision.
- **Idempotent everywhere.** Both stages upsert; safe to re-run any time. Reclassifying after tuning `phrases.json` is one command (`classify pending`); it only touches rows still in `status=pending, classification=unknown`.
"""


def _render_run_subtab() -> None:
    with st.expander("📚 how this pipeline works", expanded=False):
        st.markdown(_HOW_IT_WORKS)

    cols = st.columns([2, 2, 2, 4])
    with cols[0]:
        days = st.number_input(
            "scrape days", min_value=1, max_value=30, value=5, step=1,
            key="engagement-days",
            help="Lookback window in calendar days, INCLUDING today. days=5 = today + the four prior days.",
        )
    with cols[1]:
        headless = st.toggle("headless", value=False, key="engagement-headless",
                             help="Run Chrome headless. Disable for first runs to watch selector iteration.")
    with cols[2]:
        dry_run = st.toggle("dry-run", value=False, key="engagement-dry-run",
                            help="Write JSON dump under results/engagement/, skip Supabase upsert.")

    scrape_cmd = [str(VENV_PY), "-m", "engagement.linkedin.scrape_comments", "--days", str(int(days))]
    if headless:
        scrape_cmd.append("--headless")
    if dry_run:
        scrape_cmd.append("--dry-run")

    classify_cmd = [str(VENV_PY), "-m", "engagement.classify.rules"]

    # Horizontal container lays the three buttons inline at content width with
    # a small CSS gap (≈1rem, roughly half a button's height). Wider relative
    # columns left whitespace inside each column that bloated the visible gap.
    with st.container(horizontal=True, gap="small"):
        st.button(
            "▶ scrape LinkedIn", key="engagement-scrape-btn", type="primary",
            disabled=is_running(SCRAPE_NAME),
            on_click=start_pipeline, args=(SCRAPE_NAME, scrape_cmd),
        )
        st.button(
            "🧮 classify pending", key="engagement-classify-btn",
            disabled=is_running(CLASSIFY_NAME),
            on_click=start_pipeline, args=(CLASSIFY_NAME, classify_cmd),
        )
        st.button(
            "🔄 refresh review data", key="engagement-refresh-data",
            on_click=invalidate_caches,
        )

    # One combined log + status block — both subcommands write to it in sequence,
    # framed by start_pipeline's own "$ ..." / "# started ..." / "# exit code ..."
    # markers so the boundary between scrape and classify stays readable.
    render_combined_status_badge([SCRAPE_NAME, CLASSIFY_NAME])
    render_combined_log_panel([SCRAPE_NAME, CLASSIFY_NAME], height=480)


def run() -> None:
    # Engagement-specific filters at the top so the user has them visible
    # without the sidebar getting cluttered for the other pipeline tabs.
    with st.expander("🛡️ engagement filters + counts", expanded=False):
        col_metrics, col_filters = st.columns([3, 2])
        with col_metrics:
            render_sidebar_metrics(horizontal=True)
        with col_filters:
            platform, search = render_sidebar_filters(key_suffix="-engagement-tab")

    # st.segmented_control rather than st.tabs() — same fix as app.py's
    # top-level routing (issue #157): st.tabs() loses the active sub-tab on
    # any widget rerun triggered inside a non-default sub-tab.
    SUB_SECTIONS = ["🚀 scrape + classify", "🧑 real comments", "🤖 AI triage", "📊 commenters"]
    sub_section = st.segmented_control(
        "engagement sub-section",
        options=SUB_SECTIONS,
        default=SUB_SECTIONS[0],
        key="engagement-sub-section",
        label_visibility="collapsed",
    )
    # See app.py's app-section for why this fallback is needed — segmented_control
    # can return None for one rerun before its default is echoed back.
    sub_section = sub_section or SUB_SECTIONS[0]

    if sub_section == "🚀 scrape + classify":
        _render_run_subtab()
    elif sub_section == "🧑 real comments":
        render_real_tab(platform, search)
    elif sub_section == "🤖 AI triage":
        render_ai_tab(platform, search)
    elif sub_section == "📊 commenters":
        render_commenters_tab(platform, search)
