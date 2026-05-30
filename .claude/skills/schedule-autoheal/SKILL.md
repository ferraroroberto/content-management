---
name: schedule-autoheal
description: Run a planning scheduler and autonomously self-heal selector breakage caused by platform UI drift. Runs the scheduler (dry-run or live), classifies any failure, and for UI-drift it probes the live DOM, applies a selector-only fix, re-validates with a dry-run, and — when confident — files an issue, opens a PR, and merges end-to-end. When not confident (ambiguous DOM, login-required, or data errors) it pings the user on Slack and stops for interactive handling. Use when running the weekly planning schedulers unattended, e.g. "/schedule-autoheal all --dry-run", "/schedule-autoheal twitter --live".
---

# schedule-autoheal

**Goal:** make the weekly planning run *set-and-forget*. Run the scheduler; if it
just works, say so and stop (minimal tokens). If a step fails because a platform's
DOM drifted (an `aria-label` appeared, a button was renamed, a header was retyped),
**self-heal in place** — no human relaunch — and either land the fix end-to-end or
escalate cleanly.

Invoking this skill is explicit authorization to run the scheduler and, **only when
the confidence gate below is met**, to commit/push/PR/merge a selector-only fix.

This is a **public repo** — never put secrets, internal identifiers, or private
project references in issues, PRs, commit messages, or Slack pings.

## Arguments

`<platform|all> [--live|--dry-run] [--debug] [--skip-<platform>] [--from-result <path>]`

- `platform` ∈ `linkedin|instagram|twitter|threads|all`. `all` runs the full pipeline.
- mode defaults to `--dry-run`. `--live` actually schedules.
- `--from-result <path>` skips the run and heals from an existing
  `results/planning/<ts>-result.json` (used when the planning tab already ran it).

## Step 1 — run (unless `--from-result`)

- `all` → `& .\.venv\Scripts\python.exe planning_pipeline.py <mode> [--debug] [--skip-*]`
- single platform → `& .\.venv\Scripts\python.exe -m planning.<platform>.schedule_<platform>_posts --all-wip <mode> [--debug]`

Then read `results/planning/latest-result.json` (the machine-readable record written
by `planning_pipeline.py`; see `planning/_failure.py` for the schema fields
`status`, `detail`, `screenshot`, `failure_kind`).

## Step 2 — triage

- `verdict == "clean"` → **report "✅ it worked", stop.** Do not edit anything.
- Otherwise collect every row whose `failure_kind != "none"`.
  - `failure_kind == "ui-drift"` → **heal-eligible** (Step 3).
  - `login-required` / `data-error` / `other` → **not** heal-eligible → **escalate** (Step 5).

Heal at most **one platform per invocation cycle**; re-run to pick up the next.

## Step 3 — heal a UI-drift failure (selector-only)

1. From the failing row, note the platform, the failing step (in `detail`), and the
   `screenshot` path. Read the screenshot if helpful.
2. **Probe the live DOM:** `& .\.venv\Scripts\python.exe -m planning._probe <platform>`
   (add `--url <page>` to target a specific page). This opens the platform's real-Chrome
   session (stealth + shared-profile lock-wait are handled by the existing session helper —
   never re-inline launch args) and prints ranked `role + accessible name` candidates.
3. **Locate the broken selector:**
   - LinkedIn → `planning/linkedin/linkedin_labels.py` (centralised regex registry; keep
     the `EN | ES` alternations).
   - Twitter / Threads / Instagram → the selector is **inline** in
     `planning/<platform>/schedule_<platform>_posts.py`. Search for the old accessible
     name / role call near the failing step.
4. **Apply a selector-only edit.** Re-anchor the role/text selector on the new accessible
   name from the probe. Prefer role + name anchors; **never** anchor on a class (the
   READMEs warn class names rotate). The diff MUST be a pure selector-string change — no
   control-flow, scheduling-logic, or unrelated edits. A reviewer should read it in seconds.
5. **Re-validate:** re-run that platform `--dry-run`. Bounded retries — **max 2** heal
   attempts per row; if still failing, **escalate** (Step 5).

## Step 4 — confidence gate

**Confident** = ALL of:
- the probe yielded exactly **one** strong role/text candidate for the failing element, AND
- the post-edit `--dry-run` **passes**, AND
- the diff is a **pure selector-string** change.

### Confident → land it end-to-end

Mirror the repo's issue/PR conventions. One **separate issue per drift**.

1. `gh issue create --title "fix(<platform>): <one-line drift>" --body "<before/after selector + probe evidence + screenshot path>" --label bug --assignee @me`
2. Branch: `git checkout -b fix/<issue-n>-<slug>`
3. Commit (no AI attribution trailer):
   ```
   fix(<platform>): re-anchor <selector> after UI drift

   - <old> → <new> accessible name (probe evidence in PR)
   - dry-run validated
   ```
4. `git push -u origin <branch>`
5. `gh pr create --title "<same>" --body "Closes #<n>\n\n<before/after + probe evidence>"`
6. Wait for CI green, then merge + delete branch (`gh pr merge --merge --delete-branch`), land on `main`.

### Not confident → escalate (Step 5)

Leave the working tree clean (or the branch un-pushed) and hand off to a human.

## Step 5 — escalate via Slack, then stop

Read the Slack target from `config/config.json` → `slack.autoheal_channel`. Send the
ping through the **fleet-wide Slack bot helper** (provided by `claude-config`, available
with zero install at `~/.claude/hooks/slack_notify.py`):

```
& py "$HOME/.claude/hooks/slack_notify.py" --channel <autoheal_channel> --text "<message>"
```

Pass the bare channel id (the helper also accepts a pasted archive URL). The message must
contain: platform, failing step, the screenshot path, the probe's top candidates, and
exactly what you need decided.

Why the bot and not the Slack MCP connector: the MCP connector posts **as the user**, so
Slack never fires a notification for it and the escalation lands silently — defeating the
point of an unattended scheduler. The bot posts as a separate identity, which actually
notifies. The bot token lives in `~/.claude/settings.json` env (`SLACK_BOT_TOKEN`), never
in this repo; see `claude-config/docs/slack-workflow.md`.

If `autoheal_channel` is blank **or** the helper reports a failure (missing token, API
error), surface that plainly in the run output and still **stop**. Either way: do not
commit, push, or merge anything ambiguous.

## Guardrails (non-negotiable)

- **Selector-only edits.** Never touch control flow, scheduling logic, or anything outside
  the selector string / label module on the auto-heal path.
- **Dry-run must pass before any commit, and before any `--live`.**
- **Bounded autonomy.** Max 2 heal attempts per row; one platform healed per cycle; hard
  stop on `login-required`, `data-error`, ambiguous DOM, or anything unexpected.
- **Anti-bot discipline preserved.** Always go through the existing
  `planning/<platform>/<platform>_session.py` helpers (real Chrome, stealth, profile
  lock-wait). Never re-inline launch args.
- **Human-reviewable trail.** Every landed fix has an issue + PR with before/after selector
  and probe evidence. Never a silent push to `main` without an issue+PR.
- **Public repo hygiene.** No secrets / internal refs in any issue, PR, commit, or Slack ping.

## Verification gate (before declaring done)

- `& .\.venv\Scripts\python.exe -m py_compile <edited files>`
- the validating `--dry-run` for the healed platform passes
- `ruff check .` if configured
