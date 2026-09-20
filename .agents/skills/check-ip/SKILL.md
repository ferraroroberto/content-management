---
name: check-ip
description: Screen where my illustrations are being reused — pull ranked un-judged LinkedIn links from the check_ip store and fan them out to Sonnet workers that open each post in the browser and propose a verdict (infringement / acceptable / unclear) with the aggravators that make it worse. Proposes only; never contacts anyone and never sets my decision. E.g. "/check-ip", "/check-ip 100", "/check-ip 500", "/check-ip --image 'books pile - learn from mistakes.png'", "screen the next batch of IP matches".
---

# check-ip

**Goal:** work through the backlog of links where one of my illustrations turned up, and leave behind a proposed verdict for each so the final call is a quick yes/no instead of a cold investigation. The decision itself is never yours.

This is a **public repo** — never put a third party's name, profile URL, message thread, or case reference into an issue, PR, commit or any file you write. Those belong in the local store only. In chat is fine.

## Boundaries — read these before the first batch

1. **Never contact anyone.** No connection request, no message, no comment, no reply. Not even a draft sent anywhere.
2. **Never file a report.** No LinkedIn copyright form, no takedown, no "report post".
3. **Never write `ok` / `person` / `chat` / `report` / `fixed`.** Those five columns are the owner's ten months of manual triage. `check_ip.screen` has no code path that writes them and a regression test enforces it — don't route around that with raw SQL.
4. **Never edit the database by hand.** Every write goes through `python -m check_ip.screen verdict`.
5. You are **proposing**, and your proposal is read as a proposal. Say `unclear` freely — a wrong confident verdict costs the owner more than an honest shrug.

## The credit policy — get this right

**A post must _mention_ the owner.** His name, a tag, or a link to the original. That is the whole test.

**A visible `ROBERTOFERRARO.ART` watermark is _not_ credit on its own.** This is the single most important rule here and the easiest to get backwards: an intact watermark with no mention anywhere in the caption or comments is still an **infringement**. The watermark's *absence* is an aggravator, not its presence a defence.

## Arguments

`[N] [--image "<filename>"] [--source <platform>] [--recheck]`

- bare `/check-ip` → one batch (10 links).
- a number → that many links in total, screened in batches of 10.
- `--image` → restrict to one illustration.
- `--source` → another platform (`Twitter/X`, `Instagram`, `Facebook`, `any`). Default is LinkedIn: that is where 97% of the owner's past decisions are and the only place acting on a finding is practical.
- `--recheck` → also serve rows already screened.

## Step 1 — pre-flight

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen stats
```

No store → say so and point at `& .\.venv\Scripts\python.exe -m check_ip.migrate`. Don't run the migration unasked.

Report the queue depth before starting, so the owner knows what fraction of the backlog this run covers. At roughly 15-20s per link, 100 links is about half an hour and 500 is 2-3 hours — say which, so nobody is surprised by a long quiet stretch.

## Step 2 — dispatch workers, one at a time

You are the **orchestrator**. You do not open a browser yourself. Split `N` into batches of **10** and run them **strictly one after another**, each in its own subagent:

- **Model: Sonnet.** Never Opus for a worker — this is repetitive classification, not reasoning-heavy work, and a 500-link run is 50 of them. You, the orchestrator, stay on whatever model the owner is already sitting in; that is deliberate.
- **Never in parallel.** There is one browser and one LinkedIn account, and it is the owner's. Concurrent workers break the shared tab group outright, and concurrent browsing is the bot signature that puts a real account at risk. One at a time is a safety requirement, not a throughput preference.
- Wait for each worker to return before dispatching the next.

Hand each worker the brief below verbatim, with its own 10 rows pulled fresh:

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen next --limit 10 --json
```

Pull the *next* batch only after the previous worker has finished, so rows it screened are already excluded — that is what makes an interrupted run resumable with no bookkeeping.

### Worker brief — pass this to each subagent

> You are screening links where an illustration by Roberto Ferraro was found, to propose whether each use is an infringement. You are proposing only. **Never contact anyone, never file a report, never write to the database except through the command given below.**
>
> For each row you are given, in order:
>
> **1. Open the post.** Load the browser tools in ONE call:
> `ToolSearch("select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__browser_batch")`
> Call `tabs_context_mcp` once, then reuse one tab for the whole batch. Navigate, wait ~3-4s, screenshot at `scale: 0.5`.
>
> **2. Read the caption and the comments.** `get_page_text` returns a *comment*, not the post body — use the screenshot for the caption, and `read_page` when you need the poster's profile href. Scroll to the end of the caption and through the comments: credit is often the last line before the image, or in the poster's own first comment.
>
> **3. Judge it.** The question is: **does the post mention Roberto Ferraro?** Name, tag, or a link to the original.
> - `acceptable` — it mentions him, or it is his own post or a reshare carrying the original attribution.
> - `infringement` — no mention anywhere in the caption or the visible comments. **An intact `ROBERTOFERRARO.ART` watermark does NOT make it acceptable.** A post that says "I saw this somewhere" or hat-tips an unknown source is still an infringement: uncredited, just not dishonest.
> - `unclear` — the page won't load, sits behind a login wall, the illustration is not actually on the page (reverse-image search does return false positives), or you genuinely cannot tell. Prefer this to a guess.
>
> **4. Add the aggravators**, both only meaningful with `infringement`:
> - `--promotional` — the caption pushes the poster's own following, product, course, newsletter or service. "Follow me for more", "link in bio", a paid offer.
> - `--altered` — the image was edited: the `ROBERTOFERRARO.ART` watermark is cropped off the baseline, painted over, or otherwise gone. Compare against the bottom-right of the image; a bare baseline with no watermark means it was removed.
>
> **5. Record it immediately** — one call per row, right after judging it, never batched at the end:
> ```powershell
> & .\.venv\Scripts\python.exe -m check_ip.screen verdict --id <ID> --verdict <infringement|acceptable|unclear> --reason "<one sentence>" --poster-url "<profile URL>" [--promotional] [--altered]
> ```
> The reason is one plain sentence saying *what you saw*, not a restatement of the verdict. "No mention in the caption or the six visible comments; watermark cropped off the baseline" beats "appears to be an infringement".
>
> **Browser gotchas — these cost real time when rediscovered:**
> - **Never scroll with the cursor over the post image.** It opens the image lightbox and wedges the tab's renderer: every later screenshot returns pure black and `zoom` times out after 30s, while text extraction keeps working, so the tab looks half-alive. Scroll with the cursor over the **left margin** (around x=200).
> - If screenshots do come back black, the tab is wedged — open a fresh tab with `tabs_create_mcp` and carry on there. Don't retry the same tab.
> - **Don't close a tab in the same `browser_batch` as a navigate.** It breaks tab-group bookkeeping and the next call fails with "not in the same group". Close tabs in their own call.
> - Present as a real human session and don't hammer. **If LinkedIn shows a captcha or an "unusual activity" notice, stop immediately** and report it — do not push through, do not solve it.
>
> **Return only a compact summary**: counts by verdict, and one line per infringement (illustration → what you saw). Do not return page text or screenshots. Finish every row in this turn — do not background work and end your turn.

## Step 3 — report progress after every batch

The owner is watching a terminal. After each worker returns, print one line so the run never goes quiet:

```
batch 3/10 · screened 30 · infringement 18 (4 worst) · acceptable 9 · unclear 3 · queue 13,207
```

Keep only these counts in your own context. **Never pull the worker's page text or screenshots into the orchestrator** — that is the whole reason the work is fanned out, and a 500-link run will not fit otherwise.

## Step 4 — hand back

Summarise and stop:

- total screened, split by verdict, and how many landed at severity 3
- the worst cases as one line each: illustration → what was seen (names are fine in chat, never in a file)
- remaining queue depth
- anything that looked off — repeated 404s, a login wall, a captcha, a worker that stalled

Then point the owner at the **⚖️ check IP** tab, ordered *worst first*, to make the actual calls. Do not offer to contact anyone, and do not offer to set the verdicts yourself.

**If the run was interrupted**, say so plainly and say how many rows were completed. Nothing is lost: every verdict is written as it is made, and re-invoking `/check-ip N` picks up where this left off.
