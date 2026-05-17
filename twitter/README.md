# `twitter/` — X (Twitter) planning & scheduling

Drives X's native composer + Schedule modal at `https://x.com/home` to
schedule next week's single-image, single-text posts from the Notion
editorial database. Mirror of `instagram/` and `linkedin/`. Posting time:
**15:00 local** (Europe/Madrid).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written content into X's native
scheduler.

---

## Workflow

```mermaid
flowchart LR
    A[Notion editorial DB<br/>WIP IG ticked for next week] -->|instagram.clone_to_other_platforms| B[illustration TW + text TW + WIP TW]
    B -->|twitter.schedule_twitter_posts| C[Open x.com/home<br/>real Chrome, dedicated profile]
    C --> D[For each WIP-TW day]
    D --> E[Side-rail Post button<br/>opens composer modal]
    E --> F[Type caption via keyboard<br/>Lexical editor]
    F --> G[Upload image via<br/>input[data-testid=fileInput]]
    G --> H[Click Schedule toolbar icon]
    H --> I[Fill 6 native selects<br/>Month/Day/Year/Hour/Minute/AM-PM]
    I --> J[Confirm → final Schedule button]
    J --> K[Untick WIP TW in Notion<br/>idempotency]
```

---

## CLI

| Command | Effect |
|---------|--------|
| `python -m twitter.bootstrap_session` | One-time interactive login → saves `twitter/chrome_user_data/` (gitignored). |
| `python -m twitter.schedule_twitter_posts --date YYYYMMDD --dry-run --debug` | Single-day rehearsal up to `Confirm`, then cancels. Screenshot at `results/twitter/<day>-post-dryrun.png`. |
| `python -m twitter.schedule_twitter_posts --date YYYYMMDD --live --debug` | Single-day live schedule. |
| `python -m twitter.schedule_twitter_posts --week-start YYYY-MM-DD --live` | Schedule all WIP-TW rows in that week (Mon → Sun). |
| `python -m twitter.schedule_twitter_posts --week-start YYYY-MM-DD --live --force` | Same, but also schedule rows that already have `link TW` populated. |

Default mode (no `--live` / `--dry-run`) is governed by
`twitter.dry_run_default` in `config/config.json` (currently `true`).

---

## Notion field map (consumes the IG-clone output)

| Role | Column |
|------|--------|
| Day title (YYYYMMDD) | `day` |
| Idempotency marker (untick on success) | `Work in Progress TW` |
| Image filename source | `illustration TW` → illustrations DB → `illustration` |
| Caption | `text TW` |
| Already-posted guard (`--force` to override) | `link TW` |
| Skip-marker (Phase-2 LinkedIn scope) | `article LI` |

All TW columns are pre-populated by
`instagram/clone_to_other_platforms.py` from the IG side of the editorial
DB. The scheduler only **reads** these (and unticks WIP-TW after a
successful live run).

---

## Validated selectors (verified 2026-05-17 against `x.com/home`)

| Step | Selector | Notes |
|------|----------|-------|
| Open composer | `[data-testid="SideNav_NewTweet_Button"]` | Side-rail Post button; opens a clean modal. |
| Caption textarea | `[role="dialog"] [data-testid="tweetTextarea_0"]` | Lexical editor — type via `keyboard.type(...)`, not `.fill()`. |
| Image upload | `input[data-testid="fileInput"]` | Pre-mounted by X — `set_input_files(path)` works directly. |
| Attachment preview | `[data-testid="attachments"]` | Wait for this to appear before continuing. |
| Schedule toolbar icon | `button[data-testid="scheduleOption"]` | Tooltip "Schedule"; opens the date/time modal. |
| Date / time selects | `[role="dialog"] select` (positional) | Six native `<select>` in fixed order: Month (value=1..12) / Day / Year / Hour (value=1..12) / Minute (value=0..59) / AM-PM (value="AM"/"PM"). |
| Confirm | `[data-testid="scheduledConfirmationPrimaryAction"]` | Returns to composer; primary action button now reads "Schedule". |
| Final schedule action | `[data-testid="tweetButton"]` (with `.last` qualifier) | Submits; composer closes back to feed. |
| Modal-only dismissal scope | `[role="dialog"]` | Always scope dismissals to dialogs — page-wide `^close$` hits the side-rail Close button. |

---

## Gotchas (hard-won on first live run, 2026-05-17)

1. **Don't trust the inline composer.** `/home` shows a `What's happening?`
   textbox at the top of the feed but it can carry stale draft state across
   sessions. Always open the modal composer via
   `[data-testid="SideNav_NewTweet_Button"]` for a guaranteed-clean state.

2. **Lexical, not contenteditable.** `.fill()` on
   `[data-testid="tweetTextarea_0"]` silently does nothing — the React
   onChange doesn't fire on synthetic events in some builds. Click the
   textarea, then `page.keyboard.type(caption, delay=4)`.

3. **`select_option(label="May")` flakes; use `value="5"`.** X's Month
   `<select>` uses 1-indexed integer values
   (`<option value="5">May</option>`). The combobox-fallback path that
   clicks `<option>` elements directly DOES NOT WORK — native `<option>`s
   aren't visible/clickable like ARIA options. Index-by-position with
   `select_option(value=…)` is the reliable path.

4. **The Schedule modal contains exactly six native selects in fixed
   order.** Month, Day, Year, Hour, Minute, AM/PM. Anchor by index rather
   than by `aria-label` — X does not always set `aria-label` on the select
   elements themselves (the visible labels live in sibling `<label>`
   elements that don't bind via `for=`).

5. **Scope modal dismissals to `[role="dialog"]`.** Page-wide
   `get_by_role("button", name=/^close$/i)` clicks the side-rail nav's
   Close affordance and prevents the composer from ever opening.

6. **Hard-refresh between days.** `page.goto(feed_url)` between each row
   resets the composer state — without it, the previously-scheduled post
   sometimes lingers in the inline composer area.

7. **`link TW` idempotency.** Skip rows where `link TW` is already set
   unless `--force` is passed (matches the LinkedIn / IG pattern). Note
   `link TW` is populated downstream by `notion_update.py` after X
   publishes — not by the scheduler itself; the scheduler's idempotency
   marker is `Work in Progress TW`.

8. **`Schedule` button (final action) shows up where `Post` used to be.**
   It's still `[data-testid="tweetButton"]` — same testid, different
   label. Use `.last` to avoid ambiguity with the inline composer's button
   on the underlying feed.

---

## Files

| File | Purpose |
|------|---------|
| `bootstrap_session.py` | Interactive Chrome login → saves dedicated profile. |
| `twitter_session.py` | Playwright wrapper: persistent context, login-check, failure-screenshot helper. |
| `schedule_twitter_posts.py` | Main scheduler — Notion query, payload resolution, X UI driver. |
| `chrome_user_data/` | Dedicated Chrome profile (gitignored). Never the user's main Chrome profile — `_resolve_user_data_dir` refuses paths that look like it. |

Failure screenshots land in `results/twitter/`.
