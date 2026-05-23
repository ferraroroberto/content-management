# `threads/` — Threads planning & scheduling

Drives the Threads native composer + calendar at
`https://www.threads.com/@ferraroroberto` to schedule next week's
single-image, single-text posts from the Notion editorial database. Mirror
of `instagram/`, `linkedin/`, and `twitter/`. Posting time: **15:00 local**
(Europe/Madrid).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written content into Threads' native
scheduler.

---

## Workflow

```mermaid
flowchart LR
    A[Notion editorial DB<br/>WIP IG ticked for next week] -->|instagram.clone_to_other_platforms| B[illustration TH + text TH + WIP TH]
    B -->|threads.schedule_threads_posts| C[Open threads.com/@profile<br/>real Chrome, dedicated profile]
    C --> D[For each WIP-TH day]
    D --> E[Click 'What's new?' → New thread modal opens]
    E --> F[Type caption via keyboard]
    F --> G[Upload image via pre-mounted input[type=file]]
    G --> H[Click top-right 3-dots → Schedule…]
    H --> I[Calendar popup: pick day-cell via gridcell ancestor]
    I --> J[Fill hh + mm inputs, 24-hour]
    J --> K[Done → final Schedule button]
    K --> L[Untick WIP TH in Notion]
```

---

## CLI

| Command | Effect |
|---------|--------|
| `python -m planning.threads.bootstrap_session` | One-time interactive login → saves `threads/chrome_user_data/` (gitignored). |
| `python -m planning.threads.schedule_threads_posts --date YYYYMMDD --dry-run --debug` | Single-day rehearsal up to `Done`, then cancels. Screenshot at `results/threads/<day>-post-dryrun.png`. |
| `python -m planning.threads.schedule_threads_posts --date YYYYMMDD --live --debug` | Single-day live schedule. |
| `python -m planning.threads.schedule_threads_posts --week-start YYYY-MM-DD --live` | Schedule all WIP-TH rows in that week (Mon → Sun). |
| `python -m planning.threads.schedule_threads_posts --week-start YYYY-MM-DD --live --force` | Same, but also schedule rows that already have `link TH` populated. |

Default mode (no `--live` / `--dry-run`) is governed by
`threads.dry_run_default` in `config/config.json` (currently `true`).

---

## Notion field map (consumes the IG-clone output)

| Role | Column |
|------|--------|
| Day title (YYYYMMDD) | `day` |
| Idempotency marker (untick on success) | `work in progress TH` |
| Image filename source | `illustration TH` → illustrations DB → `illustration` |
| Caption | `text TH` |
| Already-posted guard (`--force` to override) | `link TH` |

All TH columns are pre-populated by `instagram/clone_to_other_platforms.py`
from the IG side of the editorial DB. The scheduler only **reads** these
(and unticks `work in progress TH` after a successful live run).

---

## Validated selectors (verified 2026-05-17 against `threads.com`)

| Step | Selector | Notes |
|------|----------|-------|
| Open composer | `text="What's new?"` on the profile feed | Opens the `New thread` dialog. The placeholder text re-appears inside the dialog too. |
| Caption textarea (inside dialog) | `[role="dialog"] div[contenteditable="true"]` | Lexical editor — type via `keyboard.type(...)`. |
| Image upload | `[role="dialog"] input[type="file"]` | Pre-mounted; `set_input_files(path)` works directly. |
| Top-right 3-dots | Positional JS picker (rightmost icon-only button in dialog header band, y < 80 from dialog top) | No aria-label, no test-id, no stable class. The header has 2 svg-only buttons; the rightmost is the 3-dots, the one next to it is "Drafts". |
| `Schedule…` menuitem | `get_by_text(/^schedule(…|\.\.\.)$/i)` | Threads uses both `…` (U+2026) and `...`. |
| Calendar header | text-equals `<Month> <Year>` (e.g. `"May 2026"`) | Use for navigation + sanity-check. |
| Calendar `>` (next month) | `aria-label="Next month"` (button) | Click until header matches target. |
| Day cell | `[role="gridcell"]` (size ~28×28) — find via JS, walking from a leaf `<span>` with the day digit up to its nearest `[role="gridcell"]` ancestor | The span itself is inert; the React click handler is bound on the gridcell. |
| Disambiguating duplicate day digits (e.g. `1` appears in both current and next month) | Pick lowest-lightness span color: today=255 (white text), current-month=0 (black text), grey-month=~150 | Current-month-non-today wins. |
| Time inputs | `input[placeholder="hh"]` and `input[placeholder="mm"]` | Two separate 24-hour inputs, zero-padded values. |
| Calendar Done | `get_by_role("button", name=/^done$/i)` | Commits the calendar selection. |
| Final Schedule action | `get_by_role("button", name=/^schedule$/i)` scoped to the dialog | Bottom-right of the dialog; replaces the `Post` button after Done. |
| Composer close detection | Absence of `[role="dialog"]` | Do NOT check for "New thread" text — it also appears in the side-nav. |

---

## Gotchas (hard-won on first live run, 2026-05-17)

1. **Span clicks DO NOT trigger the day handler.** The visible day digit
   lives in a leaf `<span>` (size ~13×16). The calendar's React click
   handler is bound on the ancestor `[role="gridcell"]` div (size ~28×28).
   Clicking the span looks plausible but bubbles into an inert parent
   chain — the calendar never updates the selection, the time-input still
   accepts a value, and `Done` schedules for whatever day was previously
   highlighted (i.e. **today**). On the first live run this caused all 7
   days to be silently scheduled for today instead of their targets.
   **Always click the gridcell ancestor.**

2. **Don't sort day candidates by "highest lightness".** Today's cell is
   styled white-text-on-dark-pill, so its `color` is `rgb(255,255,255)` —
   the brightest of all cells. The intuition "bright = current-month" is
   wrong; the actual semantics are:
   - today           → 255 (white on dark)
   - current month   → 0   (black on white)
   - prev/next month → ~150 (light grey)
   Pick **lowest** lightness for current-month-non-today.

3. **The 3-dots button has no aria-label and no test-id.** The dialog
   header contains exactly 2 svg-only icon buttons (no text); the
   rightmost is the more-options menu. Anchor positionally inside the
   dialog header band (y < 80 from dialog top).

4. **`aria-label*="options"` matches the wrong button.** There's a
   `Reply options` button at the bottom of the dialog whose aria-label
   contains "options". Scope to the dialog header — not the whole dialog.

5. **Composer-close detection: dialog-only, never text-based.** The
   literal text "New thread" appears in the side-nav menu as a permanent
   link, so `get_by_text("New thread").count() == 0` is **never true**.
   Use `[role="dialog"].count() == 0` instead.

6. **Time inputs are 24-hour, two-input, zero-padded.** Don't try to
   convert to 12-hour with AM/PM — just fill `hh="15"`, `mm="00"` for
   3pm.

7. **Days appear in spans, not directly on gridcells.** The gridcell
   element's `innerText` IS the day digit (because the digit's spans are
   children of the gridcell), but the gridcell's role lookup by name does
   NOT match because the accessible name is empty. Walking from leaf
   spans up to the gridcell ancestor is the reliable path.

8. **`link TH` idempotency.** Skip rows where `link TH` is already set
   unless `--force` is passed (matches the LinkedIn / IG / X pattern).
   The scheduler's primary idempotency marker is
   `work in progress TH` — unticked on a fully-successful LIVE run.

---

## Files

| File | Purpose |
|------|---------|
| `bootstrap_session.py` | Interactive Chrome login → saves dedicated profile. Threads auth bounces through `instagram.com/accounts/login` — log in there too if prompted. |
| `threads_session.py` | Playwright wrapper: persistent context, login-check (markers cover `threads.com/login` AND `instagram.com/accounts/login`), failure-screenshot helper. |
| `schedule_threads_posts.py` | Main scheduler — Notion query, payload resolution, Threads UI driver. |
| `chrome_user_data/` | Dedicated Chrome profile (gitignored). |

Failure screenshots land in `results/threads/`.
