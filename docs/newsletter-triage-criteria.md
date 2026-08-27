# Newsletter triage — selection criteria (inferred from 54 weeks)

The weekly newsletter picks 8 articles × 3 topics (leadership and management · personal development · innovation), stars one per topic and names one must-read. This document states the criteria that reproduce those picks, **each with the number it was measured on**, so a reader (or the Step-2 ranker, which consumes the machine-readable twin `results/newsletter/triage/criteria.json` — gitignored, rebuilt from the hand-written `RULES` in `newsletter/triage/criteria.py`) can tell a rule from a guess.

**Ground truth** (`python -m newsletter.triage.history`, built 2026-08-21): Gmail label `newsletters`, 2025-08-08 → 2026-08-21 — 4,383 emails (median 82/week, min 25 in the opening week, max 102), 57,849 non-noise links; Notion editions N174–N229 (58 rows, 54 complete) with 1,317 picks. 94.7% of picks were traced to the email that offered them (46% exact URL, 26% Substack slug, 8% anchor-title fuzzy, 14% by the sender's domain in the window); the 70 unmatched are mostly Rishad/Mike Fisher posts whose email anchor is an image or a one-word title.

Regenerate: `python -m newsletter.triage.history --no-gmail --reextract --no-resolve` (cached HTML, no network) then `python -m newsletter.triage.criteria`.

## 1. Shape of an edition (hard)

| Rule | Measured |
|---|---|
| 8 articles per topic, 24 per edition | 8/8/8 in 51 of 54 complete editions; 25 four times (one article with no topic), 23 once |
| exactly one ⭐ per topic | 3 stars in 51/54 editions (2 twice, 1 once) |
| one must-read = first sentence of the edition title | 48 recoverable; topic of the must-read: personal development 29 (60%), leadership 17 (35%), innovation 2 (4%) |
| the review fills the first future edition with a free slot | at build time N228 already had 12 picks and N229 had 6 — the queue runs 1–2 editions ahead |

## 2. Caps per edition

| Cap | Target | Tolerated | Histogram (56 editions) |
|---|---:|---:|---|
| HBR articles | ≤3 | 4 | 3:26 · 2:10 · 4:12 · 1:6 · 0:1 · 5:1 |
| same author | ≤2 | 3 | 2:32 · 3:14 · 1:5 · 4:3 · 5:2 — the ≥4 tail is org-level "authors" (HBR's `harvardbiz`, McKinsey), not people |
| same domain | ≤3 | 4 | 3:30 · 4:13 · 2:11 · 5:1 · 1:1 |
| picks per email | 1 | 2 | one email → one pick is the norm; only the Readwise digest (2.0/email) and HBR daily alerts routinely exceed it |

Owner-stated and confirmed: *no more than two articles from the same person in one edition*, *no more than three from HBR*.

## 3. Sender priors (who gets picked)

Hit-rate = picks per email received. Full ranked table in `criteria.json → sender_priors.ranked`; headline tiers:

- **Near-certain (≥0.9/email):** Readwise weekly digest 2.02 (113 picks / 56 emails — a curated digest of widely-highlighted articles; the single best source), Anne-Laure Le Cunff / Ness Labs 0.96, Tom Geraghty / psychsafety 0.91, Ethan Mollick 0.94, Sheril Mathews 0.92.
- **Strong (0.5–0.9):** Wes Kao 0.80, Mike Fisher 0.78, Oliver Burkeman 0.77, Rishad Tobaccowala 0.72, Rex Woodbury 0.70, Scott Young 0.65, INSEAD Knowledge 0.65, Jason Cohen 0.60, Kate Sotsenko / TheGoodBusy 0.57, Howard Yu 0.56, Gustavo Razzetti 0.53, Visual IDEAs 0.50.
- **Volume sources (low per-email, high total):** HBR 0.44 but 151 picks / 345 emails (119 leadership), Sahil Bloom 0.49 (79 picks, the weekly essay), Lenny 0.32 (39 — podcast episodes and product/AI essays), Gorick Ng 0.41, David Epstein 0.47, Cassie Kozyrkov 0.45, IMD 0.33, Superhuman AI-news 0.14 but 51 picks (41 innovation — *one* general-interest item per issue, never the release list), McKinsey 0.16 (27, reports/surveys), Tim Ferriss 0.16, Ben Thompson 0.22, Peter Diamandis 0.16.
- **Floor (≥20 emails, 0–2 picks in 54 weeks):** Esade Alumni 146, IESE Alumni 84, Designing Your Life 65, Myriam Hadnes 55, James Clear 55 (2), Matthias 45, Khe Hy 33, Explain Ideas Visually 30, Dorie Clark 30, The Generalist 29 (1), Estelle Metayer 24, Magda Teruel 22, Substack system mails. Listed in every report, ranked last, never silently dropped.

Topic mix per sender is stable (e.g. psychsafety → leadership 40/48, Ness Labs → personal development 43/49, Rex Woodbury → innovation 21/21), so the sender is a strong topic prior before reading the article.

## 4. Topic signatures

**Leadership and management** (439 picks) — psychological safety, culture/change, feedback & difficult conversations, trust, meetings/decisions, emotions and humanity at work, power and politics, delegation/accountability. Domains: hbr.org 124, fearlessculture 38, psychsafety 36, mikefisher 27, rishad 17, INSEAD 17, Wes Kao 15, IMD 14. Authors: Razzetti 39, HBR 35+19, Geraghty 33, Fisher 27, Rishad 18, Kao 16.

**Personal development** (442) — attention/focus/busyness, habits & motivation, learning & reading, meaning/optimism/happiness, mental models for oneself, career & identity, writing & thinking. Domains: sahilbloom 77, nesslabs 41, youtube 22 (talks), scotthyoung 22, thegoodbusy 22, rishad 21, hbr 19, davidepstein 16, ryanholiday 8, gorick 10, justinwelsh 7, fs.blog 4. Authors: Bloom 69, Le Cunff 33, Sotsenko 23, Young 22, Rishad 21, Epstein 16, Burkeman 11, Holiday 10.

**Innovation** (432) — AI and the future of work/organisations, agents and what they change, strategy & foresight, founders/product/growth, long-form science interviews, platform economics, charts that capture change. Domains: lennysnewsletter 23, digitalnative 22, mckinsey 19, oneusefulthing 18, mikefisher 17, youtube 17, decision.substack 15, dwarkesh 14, hbr 13, stratechery 13, x.com 12, leanfoundry 12, howardyu 11, metatrends 10. Authors: Woodbury 22, Rachitsky 22, Mollick 20, Fisher 17, McKinsey 16, Kozyrkov 15, Maurya 13, Thompson 12, Patel 11, Yu 11, Diamandis 10, Osmani 10.

Videos/podcasts count (youtube 43, x.com 18 picks); `type` stays `article`.

## 5. News policy (innovation)

Owner rule: *a new model is not relevant; a new capability or a general-interest shift is.* Measured: news-shaped titles (launch/release/announce/model names/funding…) are 3.5% of innovation picks vs 2.3% of all offered links — no over-weighting. The release-shaped picks that exist are essays about the release (Mollick "Sign of the future: GPT-5.5", Thompson "Gemini! At the disco", "Three years from GPT-3 to Gemini 3") or a single primary source per event (Google's Gemini 3 post, Anthropic research posts).

Keep: new capability class explained for a business reader · credible adoption/jobs/economy surveys (McKinsey, Stanford HAI, Gallup, HBS, Microsoft/Glean reports) · first-hand practitioner essays. Drop: funding/valuations/earnings/executive moves · benchmarks, model cards, release notes · vendor product pages · AI-news roundups as a whole (take at most the one general-interest item).

## 6. Exclusions that are not "noise"

Beyond the mechanical noise filter (unsubscribe, share, social, app stores — `newsletter/triage/gmail.py`), these anchors were offered thousands of times and picked zero times: book orders/pre-orders, courses/cohorts/workshops/masterclasses/consultations, sponsor and partner promos, job postings, "continue in the Substack app", RSS/feed links, executive-education programme brochures (IMD programme mails: 0 picks; IMD *insight* articles: 21), alumni bulletins.

## 7. Style and timing

- Titles: evergreen and conceptual — "The X effect", "Why Y", "How to Z", a named paradox/metaphor; avg 6.8 words / 41 chars. Listicles are rare; questions are fine.
- Email → edition lag: mode 10 / 17 / 20 days (review ~1 week before publication, items land in the first edition with a free slot). Anything dated >21 days before an edition is rare; the backfill pool (`next` checkbox, 28 rows; classics) covers a short topic.
- Stars concentrate on the strong-tier senders: Fisher 9, Geraghty 8, Bloom 7, Le Cunff 6, Woodbury 6, HBR 5+14 by domain, Yu 5, Mollick 5, Sotsenko 5. Must-reads by author: Geraghty 4, Le Cunff 4, Bloom 3, Burkeman 3, Sotsenko 3.

## 8. Owner rules added at review (2026-08-21)

- **No paywalled content, ever.** A paywalled pick is promotion, not content for the reader. Hard walls (Substack "for paid subscribers", subscribe-to-read) are excluded; metered/registration walls like HBR's are accessible and stay in. **Fearless Culture (Gustavo Razzetti) went paywalled in 2026 and is excluded from now on** — its 40 historical picks stay in the dataset as history, the sender is `tier: never` in `results/newsletter/triage/overrides.json` and its domain was dropped from the leadership signature list. The Step-2 engine detects paywall markers on fetch and reports them as their own state.
- **One window → one edition.** A run covers the review window (last watermark → now, normally Saturday → Friday) and fills *one* edition (8/8/8) for the next Saturday, keeping the queue one edition ahead. A backlog of N weeks is processed as N windows → N editions, oldest first — never drained into one edition.
- **Feedback loop.** The report is the review surface: tick = yes, untick = no. Ingesting the reviewed report updates sender tiers/weights in `results/newsletter/triage/overrides.json` and logs each decision to `results/newsletter/triage/feedback.jsonl`; the weekly Notion re-sync (`history.py`) refreshes the data priors, so the next run uses classification + criteria.
- **New senders.** A sender with no history is flagged NEW, scored on content + criteria only, and listed for a manual tier decision (`always|usually|rarely|never|review`) that persists in `results/newsletter/triage/overrides.json`.

## 9. What the ranker should therefore do (Step 2)

1. Score = sender prior × topic fit × title/content quality, with the news policy and exclusions as vetoes.
2. Fill 8 per topic from the candidates, enforcing HBR ≤3 (4 only if the 4th is clearly stronger than the next non-HBR), same person ≤2, same domain ≤3, one pick per email unless Readwise/HBR.
3. Suggest ⭐ per topic and a must-read from the top of personal development or leadership (innovation only with a reason) — flagged as suggestions.
4. Report every email and every link with its verdict and reason; floor senders included; unknown states (unfetchable, unscorable) kept distinct from "skipped".

## Known limits of the dataset

- Author caps are measured on Notion's `author or source`, which is sometimes an organisation (`harvardbiz`, McKinsey) or `(not classified)` (64 picks) — the person-level cap is slightly stricter than the histogram suggests.
- 6% of picks are matched to a sender by domain only (anchor ≠ title); per-sender hit-rates for Rishad, Mike Fisher, Gorick, Susan David, Justin Welsh, Marketoonist are therefore lower bounds by 1–9 picks.
- The negative set is "offered and not picked in that window"; an article offered twice and picked once counts once.
