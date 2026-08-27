---
name: newsletter-triage
description: Triage the 'newsletters' Gmail label into an edition-sized shortlist (8 × 3 topics) using the owner's inferred criteria — one review window → one edition, paywall/promo vetoes, runs stored in Supabase, sender weights + criteria notes that learn from the owner's review. E.g. "/newsletter-triage", "/newsletter-triage --days 7", "/newsletter-triage --backtest N226,N227", "/newsletter-triage --feedback results/newsletter/triage/triage-2026-08-08_2026-08-15.md", "/newsletter-triage --lessons 12".
---

# newsletter-triage

**Goal:** replace the manual inbox review with a stored, reviewable shortlist. Run the deterministic engine once, synchronously, read the report it wrote, summarise the shortlist in chat, and stop. The interactive review (tick / untick / promote, Apply, distil lessons) is the control panel's **🧭 triage** tab. The only external writes are the two explicit hand-off flags — `--open` (tabs in the `:9222` Chrome) and `--mark-reviewed` (one Notion comment) — never run without the owner asking for them in this conversation.

This is a **public repo** — never put secrets, tokens or private identifiers in issues, PRs, commits or the report.

## Arguments

`[--days N] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--force] [--backtest N226[,N227]] [--feedback <report.md>] [--lessons <run-id>] [--open <run-id>] [--mark-reviewed <run-id>] [--no-llm] [--source cache]`

- default = window from the last watermark (`results/newsletter/triage/state.json → reviewed_until`, else today−7) to today, split into 7-day windows oldest first — **one report per window, one edition per report** (owner rule: never drain the inbox into one edition).
- a window already stored in Supabase is **refused** (exit 3) unless `--force` — say so and ask before forcing; the owner's decisions survive a re-run.
- `--backtest` = replay past editions offline from the history cache (stored as `kind=backtest` with the real picks as decisions) and print precision/recall.
- `--feedback <report>` = ingest the owner's ticks from a markdown report → `triage_decisions` + `results/newsletter/triage/overrides.json` sender tiers.
- `--lessons <run-id>` = distil the applied review of a stored run into ≤ 3 proposed criteria notes (hub alias `claude_sonnet`); list them — the owner accepts them in the tab or with `-m newsletter.triage.lessons --accept <ids>`.
- `--open <run-id>` = `-m newsletter.triage.handoff --run <id> --open`: ensure the newsletter Chrome, one tab per ticked URL of the applied review (already-open tabs skipped); the unchanged `newsletter_pipeline.py archive` then takes over. With no applied review it falls back to the engine's suggested picks and says so.
- `--mark-reviewed <run-id>` = `-m newsletter.triage.handoff --run <id> --mark-reviewed`: exactly one `until <newest e-mail, Gmail style> > included` comment on the task page + `state.json → reviewed_until`. `-m newsletter.triage.handoff --run <id>` alone prints both (URLs + line) and writes nothing.

## Step 1 — pre-flight

- `results/newsletter/triage/criteria.json` exists (else `& .\.venv\Scripts\python.exe -m newsletter.triage.criteria`; that needs the history stats from `-m newsletter.triage.history`).
- `auth/gmail/token.json` exists (else point the owner to `newsletter/README.md` → Gmail one-time setup; do not run the consent flow unattended).
- Store: `& .\.venv\Scripts\python.exe -m newsletter.triage.db ensure-schema` — if it prints the apply recipe, the engine still runs **report-only** (`stats.store = unavailable`); say so explicitly, never call that "stored".
- Hub up at `http://127.0.0.1:8000` — the engine health-checks and degrades to a rule-only report (`stats.llm_calls = 0`) if not; say so.

## Step 2 — run (synchronously — no background + wait; a headless session has no wake-up)

```powershell
& .\.venv\Scripts\python.exe -m newsletter.triage.run [--days N | --since … --until …] [--force]   # live, from Gmail
& .\.venv\Scripts\python.exe -m newsletter.triage.run --backtest N226,N227 --force                  # offline replay
& .\.venv\Scripts\python.exe -m newsletter.triage.feedback <report.md>                               # ingest ticks
& .\.venv\Scripts\python.exe -m newsletter.triage.lessons --run <id>                                 # propose lessons
```

Typical live run: ~80 emails → ~1,000 links → stage-A batched scoring (~40 hub calls) → fetch + stage-B for the top-90 → `results/newsletter/triage/triage-<start>_<end>.md` + a `triage_runs` row. 5–10 minutes.

## Step 3 — report back

Read the report(s) and tell the owner, per window: the 8/8/8 shortlist (title · sender · one-line why), the ⭐/🏆 suggestions, any topic short of 8 (backfill from `next` / classics), new senders needing a tier, the run id, and the stats line (emails / links / scored / fetched / LLM calls / paywalled / unknown / store). If something could not be fetched or scored, say it is **unknown**, not "skipped".

Then point the owner to the **🧭 triage** tab to review + Apply (or, for a markdown review, `--feedback <report>`), and to 🧠 Distil once applied.

## Guardrails (non-negotiable)

- Read-only towards Notion / Gmail / Chrome unless the owner explicitly asks for `--open` / `--mark-reviewed` in this conversation (the only external writes: tabs in the `:9222` Chrome, one Notion comment); everything else writes only the Supabase `triage_*` rows, the markdown report, `results/newsletter/triage/overrides.json` / `lessons.json` when the owner decides.
- Paywalled content is never a pick (criteria §8); `results/newsletter/triage/overrides.json` `tier: never` wins over every score.
- One window → one edition. Do not merge windows. Never `--force` without saying which stored run gets replaced.
- Lessons are proposed, never auto-accepted.
- Don't re-inline Gmail / LLM / Chrome / Supabase helpers — the engine imports the existing ones (`newsletter/triage/db.py` owns every query).

## Verification gate (before declaring done)

- Report file exists and every email in the window appears once; shortlist ≤ 8 per topic; stats footer present (`store: run <id>` or `unavailable`).
- `& .\.venv\Scripts\python.exe -m unittest tests.test_triage_links tests.test_triage_criteria tests.test_triage_engine tests.test_triage_db` green when the engine code was touched.
