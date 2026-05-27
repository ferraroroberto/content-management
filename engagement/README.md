# engagement — anti-AI comment triage

Fourth pipeline in this repo (sibling to `planning/`, `reporting/`, `newsletter/`). Defends my comment threads against AI-generated noise. Two outputs:

1. **Real-comments inbox** — comments worth my personal reply, surfaced in a clean Streamlit view.
2. **AI-triage queue** — staged canned acknowledgements I one-click approve in a batch.

**Never auto-sends.** Every action is staged for explicit approval. See issue [#20](https://github.com/ferraroroberto/content-management/issues/20) for the full design + the public-repo defense-mechanism disclaimer.

## What's here

- LinkedIn only (other platforms = Phase 4).
- Scraper that reads my recent posts from the Notion editorial DB (where `link LI` is set) and walks each one in the existing `planning/linkedin/chrome_user_data` session.
- **Layered classifier**:
  1. **Rules** (Phase 1) — whitelist / blacklist / generic-praise / short / no-personal-token / emoji-only / exact-text-duplicate / sub-2-min-after-post. Tunable via `engagement/classify/phrases.json`.
  2. **Local sklearn model** (Phase 2a) — logistic regression on TF-IDF (word 1-2 + char_wb 3-5) plus six per-comment scalars, trained on accumulated commenter-level whitelist/blacklist labels. Called only on rows the rules layer left as `unknown`. Lossless when not trained yet — pipeline behaves identically to rules-only.
  3. **LLM fallback** (Phase 3) — routed through the local LLM hub (`http://127.0.0.1:8000`, version-free alias `claude_haiku`). Fires only on rows where the local model's `P(AI)` sits in the uncertainty window `[llm_fallback_local_uncertainty_low, local_model_ai_threshold)` — i.e. the ambiguous middle the cheap layers can't resolve. Verdicts are cached on disk (`engagement/classify/.llm_cache.json`, gitignored) keyed by `sha256(commenter_url|text)`; database is the truth-of-record.
- **Reputation feedback loop** (Phase 3) — `engagement.reputation.update` recomputes rolling per-commenter `signals`, `counters`, and `reputation_score` from accumulated comment history and upserts them into `commenters`. Pure batch, idempotent, preserves manual whitelist/blacklist labels.
- Two Supabase tables (`commenters` + `comments`) — see `engagement/db/schema.sql`.
- Streamlit review app — `engagement/review_app.py`.
- **No auto-posting worker, ever.** Approved rows sit in Supabase; you copy the suggested reply via `st.code`'s built-in copy button and paste it into LinkedIn's native composer yourself. The originally-planned Phase 2b send worker is permanently out of scope on TOS grounds (see issue [#20](https://github.com/ferraroroberto/content-management/issues/20)).

## Workflow

```mermaid
flowchart LR
    A[Notion editorial DB<br/>link LI set<br/>date >= today - N] --> B[engagement.linkedin.scrape_comments]
    B --> C[(Supabase<br/>comments + commenters)]
    C --> D[engagement.classify.rules]
    D --> L{score &lt; threshold?}
    L -- yes --> M[engagement.classify.local_model<br/>logreg, P(AI)]
    L -- no --> C
    M --> C
    C --> E[engagement.review_app<br/>Streamlit]
    E -.approve.-> C
    E -.blacklist/whitelist.-> C
```

## CLI

| Command | Purpose |
|---|---|
| `python -m engagement.linkedin.scrape_comments --days 5` | Scrape last 5 calendar days **including today** of LI comments → Supabase. Headful by default. `--days N` means N total days, so `--days 1` = today only. |
| `python -m engagement.linkedin.scrape_comments --days 1 --limit 1 --dry-run` | Smoke test against 1 post (today only); writes `results/engagement/dryrun-*.json` instead of upserting. |
| `python -m engagement.classify.rules` | Re-classify all `pending` `unknown` rows using current `phrases.json`. If a local model is on disk, it runs as a second pass on the rows the rules layer left as `unknown`. |
| `python -m engagement.classify.local_model train` | Train the local sklearn classifier on accumulated whitelist/blacklist labels. Refuses to train below `rules.local_model_min_train_per_class` per class. Writes `engagement/classify/local_model.joblib` (gitignored) + a metadata sidecar. |
| `python -m engagement.classify.local_model eval` | 5-fold stratified CV on the same labeled set; prints AUC + precision/recall/F1 at the configured threshold. |
| `python -m engagement.reputation.update` | Recompute rolling per-commenter signals + counters + reputation_score from the `comments` table and upsert into `commenters`. Idempotent; preserves manual whitelist/blacklist. |
| `& .\.venv\Scripts\python.exe -m streamlit run engagement\review_app.py` | Launch the review UI on `http://localhost:8501`. |

## One-time setup

1. Install deps: `& .\.venv\Scripts\python.exe -m pip install -r requirements.txt`
2. Apply schema: open the Supabase dashboard → **SQL Editor** → paste `engagement/db/schema.sql` → **Run**. Idempotent.
3. Make sure `planning/linkedin/chrome_user_data/` exists (the engagement scraper reuses it). If not: `& .\.venv\Scripts\python.exe -m planning.linkedin.bootstrap_session`.

No new secrets needed — uses `config.supabase.service_role_key` and `config.notion.api_token` that the rest of the repo already uses.

## Gotchas

- **Selectors are unvalidated on first run.** The LinkedIn comment-area DOM was never used by the planning composer. `scrape_comments.py` has multiple candidate selectors per concept (`SEL_COMMENT_ARTICLE`, `SEL_COMMENT_TEXT`, etc.) and logs which one matched. If a post returns 0 comments, expect to iterate the selector lists.
- **Headful by default.** Headless would be brittle for first-run selector debugging. Use `--headless` once selectors are validated.
- **Idempotent.** Both tables are upserted on `(platform, comment_id)` and `(platform, account_url)` respectively — safe to re-run on the same posts.
- **Relative time parsing is approximate.** LinkedIn shows `2h`, `5m`, `1d`. We reconstruct an absolute timestamp from scrape time, so `posted_at` drifts up to ~one tick of the LI display unit. Fine for cadence rules.
- **Unknown ≠ AI.** Comments that score below the AI threshold (and below the local-model threshold, if a model is loaded) stay `unknown` and surface in the real-comments tab by default. Bias is toward human review — better to over-surface than wrongly stage a canned reply.
- **Local model is lossless when missing.** If `local_model.joblib` isn't on disk yet, the rules pass is the only classifier and the verdict reasons match Phase 1 exactly. Train it by running `python -m engagement.classify.local_model train` once you have ≥20 whitelist + ≥20 blacklist commenters.
- **Featurizer is the single source of truth.** `local_model.featurize_one` re-imports `_seconds_after`, `_is_emoji_only`, `_generic_praise_hits`, `_has_personal_token` from `rules.py` so train-time and inference-time signals can never drift. If you tune one of those rules in `phrases.json` (or the helper logic), retrain before relying on the model.
- **Whitelist/blacklist cascades.** Marking a commenter triggers a retroactive reclassification of their pending comments (`cascade_blacklist_pending` / `cascade_whitelist_pending`).
- **No tests yet.** Single-user pipeline; verification is a real scrape + manual eyeball.

## Files

```
engagement/
├── __init__.py
├── README.md                         # this file
├── linkedin/
│   └── scrape_comments.py            # Notion → LI post URLs → comment DOM → Supabase
├── classify/
│   ├── rules.py                      # rule pipeline + verdict writer; calls local_model + llm_fallback on unknowns
│   ├── local_model.py                # sklearn logreg, trained on accumulated wl/bl labels
│   ├── llm_fallback.py               # Phase 3 — local-hub LLM, fires only inside the local-model uncertainty window
│   ├── phrases.json                  # generic-praise list, weights, thresholds, reply templates
│   ├── local_model.joblib            # (gitignored) trained pipeline — user-specific artifact
│   ├── local_model.json              # (gitignored) training metadata sidecar
│   └── .llm_cache.json               # (gitignored) per-machine cache of LLM verdicts
├── reputation/
│   └── update.py                     # rolling signals + reputation_score recomputer (Phase 3)
├── db/
│   ├── client.py                     # supabase-py + notion client + CRUD helpers
│   └── schema.sql                    # one-time DDL for commenters + comments
└── review_app.py                     # Streamlit UI
```

## Config block (in `config/config.json`)

```json
"engagement": {
    "default_days": 5,
    "phrases_path": "engagement/classify/phrases.json",
    "platforms_enabled": ["linkedin"],
    "linkedin": {
        "expand_max_clicks": 30,
        "expand_settle_ms": 1200,
        "page_settle_ms": 2500
    },
    "llm_fallback": {
        "enabled": true,
        "base_url": "http://127.0.0.1:8000",
        "model": "claude_haiku",
        "cache_path": "engagement/classify/.llm_cache.json",
        "timeout_seconds": 20
    }
}
```

## Phase status

- **Phase 1** ✅ shipped — rules classifier + LI scraper + Streamlit review UI.
- **Phase 2a** ✅ shipped — local sklearn classifier. Train it when you have ≥20 labeled commenters per class.
- **Phase 2b** ❌ permanently out of scope — auto-send worker. Violates LinkedIn TOS §8.2. The pipeline stays staged + manual copy-paste.
- **Phase 3** ✅ shipped — LLM fallback (local LLM hub, `claude_haiku` alias) for the ambiguous middle, plus rolling `commenters.signals` / `counters` / `reputation_score` recomputation via `engagement.reputation.update`.
- **Phase 4** ❌ dropped from scope (2026-05-27) — other platforms (IG/TW/TH/SB). Engagement pipeline stays LinkedIn-only.
- **Phase 5** ❌ dropped from scope (2026-05-27) — cross-platform identity linking. Folds under Phase 4 and is dropped with it.
