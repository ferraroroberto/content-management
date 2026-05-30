# Self-healing scheduler

The planning schedulers drive live third-party web UIs (LinkedIn, X, Instagram,
Threads) whose DOM drifts almost weekly — an `aria-label` appears, a header is
retyped, a toolbar relocates — silently breaking a selector. This page documents
the loop that turns that break-and-fix cycle into a set-and-forget operation.

## The flow

1. The **planning tab** (`app/tab_planning.py`) has a **🔧 run + autoheal
   (console)** button. It launches `app/autoheal_console.py` in a **visible,
   detached console** (`CREATE_NEW_CONSOLE`) running the `/schedule-autoheal`
   skill headless. The window shows the live run; the in-app panel mirrors it by
   tailing the tee'd log file. `launch_autoheal.bat` is the equivalent manual
   entry point.
2. The skill runs the scheduler (or `planning_pipeline.py`) and reads the
   machine-readable result at `results/planning/latest-result.json`.
3. **Clean run → "it worked", stop.** Minimal tokens, no intervention.
4. **UI-drift failure → self-heal in place**: probe the live DOM, apply a
   selector-only fix, re-validate with a dry-run.
   - **Confident** → file a drift issue, branch, commit, PR (`Closes #N`),
     merge, delete branch, land on `main` — the whole issue lifecycle,
     autonomously. One issue per drift.
   - **Not confident** → ping the user on Slack and stop for interactive
     handling.

## The machine-readable contract

`planning_pipeline.py` writes, alongside the human-readable markdown summary, a
structured `results/planning/<ts>-result.json` plus a stable
`latest-result.json` pointer. Each platform row carries:

| field          | meaning                                                        |
|----------------|----------------------------------------------------------------|
| `status`       | scheduler row status (`LIVE`/`DRY`/`FAIL`/`LOGIN-REQUIRED`/…)   |
| `detail`       | free-text error / status detail                                |
| `screenshot`   | failure screenshot path, lifted out of `detail`                |
| `failure_kind` | heal-eligibility class from `planning/_failure.py`             |

## Failure classification — `planning/_failure.py`

`classify(status, detail)` is a pure function imported by **both** the pipeline
(which stamps `failure_kind`) and the skill (which reads it), so the two can
never disagree.

| `failure_kind`   | trigger                                          | action            |
|------------------|--------------------------------------------------|-------------------|
| `ui-drift`       | Playwright timeout / locator / selector phrasing | **auto-heal**     |
| `login-required` | session logged out                               | escalate (human)  |
| `data-error`     | payload / caption / illustration / Notion miss   | escalate (human)  |
| `other`          | anything unclassified                            | escalate (human)  |
| `none`           | not a failure (`LIVE`/`DRY`)                      | —                 |

Only `ui-drift` is auto-fixed, and only with **selector-only** edits.

## The probe — `planning/_probe.py`

`python -m planning._probe <platform>` opens the platform's live page through its
existing `planning/<platform>/<platform>_session.py` helper (real Chrome,
stealth, shared-profile lock-wait — never re-inlined) and dumps the accessibility
tree as a ranked list of `role + accessible name` candidates — exactly the shape
a Playwright `get_by_role(role, name=...)` selector needs. Class-based candidates
are deliberately omitted: the per-platform READMEs warn class names rotate, so a
class-anchored fix would re-break next week.

Selector edit targets differ by platform: LinkedIn centralises selectors in
`planning/linkedin/linkedin_labels.py`; Twitter / Threads / Instagram keep them
inline in `schedule_<platform>_posts.py` (centralising those is tracked as
follow-up work).

## The confidence gate

A fix lands end-to-end only when **all** hold: exactly one strong role/text
candidate, the validating dry-run passes, and the diff is a pure selector-string
change. Otherwise the skill escalates via the **Slack MCP**
(`mcp__claude_ai_Slack__slack_send_message`) to the channel in
`config/config.json` → `slack.autoheal_channel`, and stops.

## The visible-console + tee mechanism

Borrowed from the `app-launcher` sister project (its `codebase-audit-fleet` job;
see that repo's `docs/lessons-launcher-owned-pty.md`):

- **`--verbose` is load-bearing.** `claude -p` buffers its result without it, so
  the console looks dead until the very end. With `--verbose` it streams
  turn-by-turn activity live.
- **`--permission-mode bypassPermissions`** lets the unattended heal edit files,
  run `gh`, and call MCP tools with no human at the prompt.
- **`--output-format stream-json` is what actually streams.** Plain
  `claude -p --verbose` (text format) **block-buffers** and flushes its whole
  output at exit — the console looks dead for the entire run, then dumps
  everything at the end. `--output-format stream-json` (requires `--verbose`)
  emits one JSON event the instant each step happens (init, each assistant
  thinking/text/tool_use block, each tool_result, the final result).
  `app/autoheal_console.py` reads those events with `readline()` and
  pretty-prints each into a readable live feed (`💭` thinking, `🔧` tool calls,
  `↳` results), teed to **both** the visible console and a log file the app
  tails.
- **Detached ≠ untracked.** The console is spawned `CREATE_NEW_CONSOLE` (visible,
  independent) but the `Popen` handle is kept so the run stays listable/killable.
- **Remote Control for mobile.** The invocation adds `--remote-control autoheal`
  so the run is also viewable/drivable from the Claude mobile/web app — handy for
  answering a "not confident" escalation from the phone. Toggle off with
  `--no-remote-control` on `app/autoheal_console.py`.

## Guardrails

- Selector-only edits — never control flow or scheduling logic on the heal path.
- Dry-run must pass before any commit and before any `--live`.
- Bounded autonomy — max 2 heal attempts per row, one platform per cycle; hard
  stop on login / data / ambiguous DOM.
- Anti-bot discipline preserved — always through the existing session helpers.
- Human-reviewable trail — every landed fix has an issue + PR with before/after
  selector and probe evidence; never a silent push to `main`.
