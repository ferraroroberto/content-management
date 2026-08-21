---
name: newsletter-triage
description: Triage the 'newsletters' Gmail label into an edition-sized markdown shortlist (8 × 3 topics) using the owner's inferred criteria — one review window → one edition, paywall/promo vetoes, sender weights that learn from the reviewed report. E.g. "/newsletter-triage", "/newsletter-triage --days 7", "/newsletter-triage --backtest N226,N227", "/newsletter-triage --feedback results/newsletter/triage/triage-2026-08-08_2026-08-15.md".
---

# newsletter-triage

**Goal:** replace the manual inbox review with a report the owner validates. Run the deterministic engine once, synchronously, read the report it wrote, summarise the shortlist in chat, and stop. Never write to Notion, Gmail or Chrome from this skill (that is `--open` / `--mark-reviewed`, issue #212).

This is a **public repo** — never put secrets, tokens or private identifiers in issues, PRs, commits or the report.

## Arguments

`[--days N] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--backtest N226[,N227]] [--feedback <report.md>] [--no-llm] [--source cache]`

- default = window from the last watermark (`results/newsletter/triage/state.json → reviewed_until`, else today−7) to today, split into 7-day windows oldest first — **one report per window, one edition per report** (owner rule: never drain the inbox into one edition).
- `--backtest` = replay past editions offline from the history cache and print precision/recall.
- `--feedback <report>` = ingest the owner's ticks (yes/no) → `newsletter/triage/overrides.json` sender tiers + `results/newsletter/triage/feedback.jsonl`.

## Step 1 — pre-flight

- `newsletter/triage/criteria.json` exists (else `& .\.venv\Scripts\python.exe -m newsletter.triage.criteria`; that needs the history stats from `-m newsletter.triage.history`).
- `auth/gmail/token.json` exists (else point the owner to `newsletter/README.md` → Gmail one-time setup; do not run the consent flow unattended).
- Hub up at `http://127.0.0.1:8000` — the engine health-checks and degrades to a rule-only report (`stats.llm_calls = 0`) if not; say so.

## Step 2 — run (synchronously — no background + wait; a headless session has no wake-up)

```powershell
& .\.venv\Scripts\python.exe -m newsletter.triage.run [--days N | --since … --until …]      # live, from Gmail
& .\.venv\Scripts\python.exe -m newsletter.triage.run --backtest N226,N227                    # offline replay
& .\.venv\Scripts\python.exe -m newsletter.triage.feedback <report.md>                        # ingest ticks
```

Typical live run: ~80 emails → ~1,000 links → stage-A batched scoring (~40 hub calls) → fetch + stage-B for the top-90 → `results/newsletter/triage/triage-<start>_<end>.md`. 5–10 minutes.

## Step 3 — report back

Read the report(s) and tell the owner, per window: the 8/8/8 shortlist (title · sender · one-line why), the ⭐/🏆 suggestions, any topic short of 8 (backfill from `next` / classics), new senders needing a tier, and the stats line (emails / links / scored / fetched / LLM calls / paywalled / unknown). If something could not be fetched or scored, say it is **unknown**, not "skipped".

After the owner reviews: remind them to run `--feedback <report>` (or do it when asked) so the next run uses classification + criteria.

## Guardrails (non-negotiable)

- Read-only: no Notion writes, no Gmail writes, no Chrome tabs from this skill.
- Paywalled content is never a pick (criteria §8); `overrides.json` `tier: never` wins over every score.
- One window → one edition. Do not merge windows.
- Don't re-inline Gmail / LLM / Chrome helpers — the engine imports the existing ones.

## Verification gate (before declaring done)

- Report file exists and every email in the window appears once; shortlist ≤ 8 per topic; stats footer present.
- `& .\.venv\Scripts\python.exe -m unittest tests.test_triage_links tests.test_triage_criteria tests.test_triage_engine` green when the engine code was touched.
