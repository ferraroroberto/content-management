---
name: check-ip
description: Screen where my illustrations are being reused — pull ranked un-judged LinkedIn links from the check_ip store and fan them out to Sonnet workers that open each post in the browser and judge it against the three conditions of my licence (credit / non-commercial / unmodified). Proposes only; never contacts anyone and never sets my decision. E.g. "/check-ip", "/check-ip 100", "/check-ip 500", "/check-ip --image 'books pile - learn from mistakes.png'", "screen the next batch of IP matches".
---

# check-ip

**Goal:** work through the backlog of links where one of my illustrations turned up, and leave behind a proposed verdict for each so the final call is a quick yes/no instead of a cold investigation. The decision itself is never yours.

This is a **public repo** — never put a third party's name, profile URL, message thread, or case reference into an issue, PR, commit or any file you write. Those belong in the local store only. In chat is fine.

## Boundaries — read these before the first batch

1. **Never contact anyone.** No connection request, no message, no comment, no reply. Not even a draft sent anywhere.
2. **Never file a report.** No LinkedIn copyright form, no takedown, no "report post".
3. **Never write `ok` / `person` / `chat` / `report` / `fixed`.** Those five columns are the owner's ten months of manual triage. `check_ip.screen` has no code path that writes them and a regression test enforces it — don't route around that with raw SQL.
4. **Never edit the database by hand.** Every write goes through `python -m check_ip.screen verdict`.
5. You are **proposing**, and your proposal is read as a proposal. Say you could not tell, freely — a wrong confident verdict costs the owner more than an honest shrug.

## The licence — three conditions, judged separately

The illustrations are published under **CC BY-NC-ND 4.0**. Three conditions, and a post has to satisfy **all three**:

| | condition | what satisfies it |
|---|---|---|
| **BY** | credit | the post names him, tags him, links to the original, his own comment claims it, or it is a reshare carrying the original credited caption |
| **NC** | non-commercial | no promotional call to action **and** no paid or business context — judged from the post itself, never from the poster's profile |
| **ND** | no derivatives | the image is not substantially edited: no crop, no filter, no added logo or text block, no redraw, no translation, signature intact. In-image wording that differs from the file on record is not an edit |

**Each is recorded on its own, with three possible answers: `met`, `violated`, or `unknown`.** `unknown` is a real answer — it means you looked and the post did not let you establish that condition. It is *not* a pass, and the store, the counters and the review tab all keep it apart from one. Never guess a condition to `met` to finish a row: an honest `unknown` hands the row to the owner to judge, while a wrong `met` retires it as compliant forever.

**Answer all three on every row.** A condition you leave out is stored as *never assessed*, which is a different state from `unknown`: it says nobody has looked yet, so the row stays in the re-screen queue and will be served to another worker. That is right when you truly could not open the post, and wrong — it burns a whole second batch on a post you already read — when you did look and simply could not tell. Say `unknown` then (issue #305).

The verdict follows from the three conditions and the CLI refuses a combination that contradicts them:

- **`infringement`** — at least one condition violated. Severity is how many: 1, 2 or 3.
- **`acceptable`** — all three met. Anything less is not acceptable, it is unassessed.
- **`unclear`** — nothing violated but something not established, whether `unknown` or unanswered. Needs `--outcome`:
  - `ambiguous` — another look could settle it (login wall, truncated caption, you genuinely could not tell).
  - `nothing_to_assess` — the post is gone, or carries no illustration of his at all. Permanent; the tab stops offering it.

### The mistakes that have actually happened

These are corrections from live runs. They cost real rework, so they live here rather than in a dispatch prompt.

- **A visible `ROBERTOFERRARO.ART` watermark is not credit.** The easiest rule to get backwards. An intact watermark with no mention anywhere is still a **BY violation**. The watermark's *absence* is what counts against a post (an ND violation), not its presence in its favour.
- **Credit and commercial use are independent.** A post that credits him properly and is plainly a company page's marketing or a paid training, workshop or conference deck is an **infringement on NC** — `--credit met --noncommercial violated`. Being paid for the context is commercial under the terms; no call to action is required — but the paid context has to be visible in the post.
- **NC is judged from the post, not the profile.** A worker once took a poster's services page, follower count and "top content creator" badge as proof of commercial use and marked NC violated on a whole batch of ten posts that contained no promotional content at all, moving every row from severity 1 to 3. The owner's ruling: *use only the evidence of the post.* NC is violated only by something **in the post** — a call to action for the poster's own following, product, course, newsletter or service; a visible paid or sponsored context; a training, workshop or conference deck; or a post published by a company or agency page. The poster's occupation, services page, follower count or personal-brand positioning is not evidence. If the post itself shows nothing, NC is `unknown`, not `violated`: an `unknown` costs a re-screen, a wrong `violated` puts a person at the top of the owner's worst-first list on evidence nobody established.
- **ND is broader than "the watermark was removed".** Any crop, filter, added logo or text block, redraw or translation breaks it, even with the signature untouched.
- **ND is substantial editing, not any difference.** A worker once read a one-word difference in the in-image caption as an edit and recorded ND violated on two posts whose watermark was fully intact. The owner keeps alternate caption versions of the same composition, so a wording difference is far more likely one of his own versions than someone else's edit. His ruling: *editing is substantial editing or translation.* A different wording on an otherwise untouched image is ND `met` — see the reused-compositions note below.
- **Authorship has exactly two routes.** The post shows the same image as `local_image` (same composition *and* the same in-image text), **or** it visibly carries the `ROBERTOFERRARO.ART` watermark. Anything else is not assessable. Never reason from artistic style, from "the same series", or from the same idea or wording appearing — many illustrators draw minimalist business graphics, and another artist can illustrate the same concept with the same caption.
- **Someone else's name or watermark on the image settles nothing by itself.** Compare against the file on record first: if composition and in-image text match, it is his work with the credit replaced, which is an **ND violation** as well as a BY one. If they do not match, it is probably that artist's own work. Both errors have occurred in live runs, in opposite directions.
- **Never revise an earlier verdict — or judge a condition — on a theory about the poster.** A poster who credited him on nine posts earns no presumption on the tenth, in either direction, and who the poster is does not make a post commercial. Judge the post in front of you.
- **A bare source hint is not credit.** "Pic from IG", "credit: unknown", "seen somewhere" — BY violated, just not dishonestly.
- **Posts are often carousels.** The matched illustration may be any slide, not the cover.
- **He reuses his own compositions with different in-image captions.** Same template plus different wording is not a file match, but such a post usually still carries the watermark. Read this together with the ND test: a watermarked post whose only difference from the file on record is the wording is carrying one of his versions unmodified — ND `met`, not violated.

## Arguments

`[N] [--image "<filename>"] [--source <platform>] [--recheck]`

- bare `/check-ip` → one batch (10 links).
- a number → that many links in total, screened in batches of 10.
- `--image` → restrict to one illustration.
- `--source` → another platform (`Twitter/X`, `Instagram`, `Facebook`, `any`). Default is LinkedIn: that is where 97% of the owner's past decisions are and the only place acting on a finding is practical.
- `--recheck` → drain the **not fully assessed** backlog instead of the pending queue: rows judged under the old credit-only question, where two of their three conditions were never looked at. It runs `screen next --unassessed`, which serves *only* those rows — the count `stats` reports as `not_fully_assessed`. Do **not** reach for `--include-screened`: that flag only stops excluding screened rows, so the backlog competes with every pending row under the same ranking and is never reached (issue #300).

## Step 1 — pre-flight

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen stats
```

No store → say so and point at `& .\.venv\Scripts\python.exe -m check_ip.migrate`. Don't run the migration unasked.

Report the queue depth before starting, so the owner knows what fraction of the backlog this run covers. Report `not_fully_assessed` too — those are rows that look screened but are not, and they are a second backlog behind the first. At roughly 15-20s per link, 100 links is about half an hour and 500 is 2-3 hours — say which, so nobody is surprised by a long quiet stretch.

## Step 2 — dispatch workers, one at a time

You are the **orchestrator**. You do not open a browser yourself. Split `N` into batches of **10** and run them **strictly one after another**, each in its own subagent:

- **Model: Sonnet.** Never Opus for a worker — this is repetitive classification, not reasoning-heavy work, and a 500-link run is 50 of them. You, the orchestrator, stay on whatever model the owner is already sitting in; that is deliberate.
- **Never in parallel.** There is one browser and one LinkedIn account, and it is the owner's. Concurrent workers break the shared tab group outright, and concurrent browsing is the bot signature that puts a real account at risk. One at a time is a safety requirement, not a throughput preference.
- Wait for each worker to return before dispatching the next.

Hand each worker the brief below verbatim, with its own 10 rows pulled fresh:

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen next --limit 10 --json
```

Under `--recheck`, add `--unassessed` to that command. Each served row then also carries `screen_verdict` and `screen_reason` — the opinion this pass is replacing. Hand them to the worker as context, not as an answer: it re-judges all three conditions from the page, and `record_verdict` preserves the old opinion in `screen_history` either way (issue #301).

Pull the *next* batch only after the previous worker has finished, so rows it screened are already excluded — that is what makes an interrupted run resumable with no bookkeeping. This holds for `--recheck` too: a re-screened row leaves the `--unassessed` set as soon as its three conditions are recorded, so the backlog drains to zero batch by batch.

### Worker brief — pass this to each subagent

> You are screening links where an illustration by Roberto Ferraro was found, to propose whether each use breaches his licence. You are proposing only. **Never contact anyone, never file a report, never write to the database except through the command given below.**
>
> His illustrations are published under **CC BY-NC-ND 4.0**: the post must **credit** him, must **not** be commercial, and must **not** modify the image. You judge all three separately, and `unknown` is a real answer for any of them.
>
> For each row you are given, in order:
>
> **1. Open the post.** Load the browser tools in ONE call:
> `ToolSearch("select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__browser_batch")`
> Call `tabs_context_mcp` once, then reuse one tab for the whole batch. Navigate, wait ~3-4s, screenshot at `scale: 0.5`.
>
> **2. Read the caption and the comments.** `get_page_text` returns a *comment*, not the post body — use the screenshot for the caption, and `read_page` when you need the poster's profile href. Scroll to the end of the caption and through the comments: credit is often the last line before the image, or in the poster's own first comment.
>
> **3. Confirm it is even his work.** Two routes only: the post shows the same image as `local_image` — same composition **and** the same in-image text — or the image visibly carries the `ROBERTOFERRARO.ART` watermark. Style, subject, "same series" or the same idea in the caption prove nothing; plenty of illustrators draw minimalist business graphics. If neither route holds, record `--verdict unclear --outcome nothing_to_assess` and move on.
>
> **4. Judge the three conditions.**
> - **credit (BY)** — `met` if the caption or the visible comments name him, tag him, link to the original, or his own comment claims it, or the post is a reshare carrying the original credited caption. `violated` if there is no mention anywhere, including when the `ROBERTOFERRARO.ART` watermark is fully intact — **an intact watermark is not credit**. A bare source hint ("pic from IG", "credit: unknown") is also `violated`. `unknown` only if you could not read the caption or the comments.
> - **non-commercial (NC)** — judge **only from the evidence in the post**. `violated` if the post itself carries a call to action for the poster's own following, product, course, newsletter or service ("follow me for more", "link in bio", a paid offer), a visible paid or sponsored context, or is a training, workshop or conference deck, or is published by a company or agency page. The poster's occupation, services page, follower count or personal-brand positioning is **not** evidence — never open the profile to decide NC. `met` if it is a personal post with no promotional or paid context. `unknown` if the post itself shows nothing either way.
> - **unmodified (ND)** — `violated` on substantial editing: any crop, filter, added logo or text block, a redraw, a translation of the in-image text, or a watermark cropped off the baseline, painted over or removed. In-image wording that differs from the file on record is **not** a modification — he keeps alternate caption versions of the same composition, so an otherwise untouched image with different wording is `met`. Compare against the bottom-right of the image; a bare baseline with no watermark means it was removed. Also `violated` when someone else's name or watermark sits on an image whose composition and in-image text match the file on record — that is his work with the credit replaced. `met` if the image matches the file on record untouched. `unknown` if the image is too small or cropped by the page to compare.
>
> **5. Record it immediately** — one call per row, right after judging it, never batched at the end:
> ```powershell
> & .\.venv\Scripts\python.exe -m check_ip.screen verdict --id <ID> \
>     --verdict <infringement|acceptable|unclear> \
>     --credit <met|violated|unknown> \
>     --noncommercial <met|violated|unknown> \
>     --unmodified <met|violated|unknown> \
>     [--outcome <ambiguous|nothing_to_assess>] \
>     --reason "<one sentence>" --poster-url "<profile URL>"
> ```
> The verdict must agree with the conditions or the command refuses the write and exits non-zero: `infringement` if any condition is `violated`, `acceptable` only if all three are `met`, `unclear` otherwise — and `unclear` needs `--outcome`. If it refuses, fix your own answer; never soften a condition to make a verdict fit.
>
> A condition you omit is recorded as *never assessed* — the safe default, because it claims nothing, but it also leaves the row in the re-screen queue for another worker. **Pass all three explicitly on every row**, using `unknown` where you looked and could not tell, so the record says what you actually looked at and the row does not come back.
>
> The reason is one plain sentence saying *what you saw*, not a restatement of the verdict. "Credited in the caption, but the account is an agency selling the same workshop the post advertises" beats "appears to be an infringement".
>
> **Browser gotchas — these cost real time when rediscovered:**
> - **Never scroll with the cursor over the post image.** It opens the image lightbox and wedges the tab's renderer: every later screenshot returns pure black and `zoom` times out after 30s, while text extraction keeps working, so the tab looks half-alive. Scroll with the cursor over the **left margin** (around x=200).
> - If screenshots do come back black, the tab is wedged — open a fresh tab with `tabs_create_mcp` and carry on there. Don't retry the same tab.
> - **Don't close a tab in the same `browser_batch` as a navigate.** It breaks tab-group bookkeeping and the next call fails with "not in the same group". Close tabs in their own call.
> - Present as a real human session and don't hammer. **If LinkedIn shows a captcha or an "unusual activity" notice, stop immediately** and report it — do not push through, do not solve it.
>
> **Return only a compact summary**: counts by verdict, how many rows you left with an `unknown` condition, and one line per infringement (illustration → which condition broke and what you saw). Do not return page text or screenshots. Finish every row in this turn — do not background work and end your turn.

## Step 3 — report progress after every batch

The owner is watching a terminal. After each worker returns, print one line so the run never goes quiet:

```
batch 3/10 · screened 30 · infringement 18 (4 worst) · acceptable 6 · unclear 6 · 9 left unassessed · queue 13,207
```

Keep only these counts in your own context. **Never pull the worker's page text or screenshots into the orchestrator** — that is the whole reason the work is fanned out, and a 500-link run will not fit otherwise.

## Step 4 — hand back

Summarise and stop:

- total screened, split by verdict, and how many landed at severity 2 and 3
- how many rows still carry an `unknown` condition, and how many came back `nothing_to_assess`
- the worst cases as one line each: illustration → which conditions broke and what was seen (names are fine in chat, never in a file)
- remaining queue depth
- anything that looked off — repeated 404s, a login wall, a captcha, a worker that stalled

Then point the owner at the **⚖️ check IP** tab, ordered *worst first*, to make the actual calls. Do not offer to contact anyone, and do not offer to set the verdicts yourself.

**If the run was interrupted**, say so plainly and say how many rows were completed. Nothing is lost: every verdict is written as it is made, and re-invoking `/check-ip N` picks up where this left off.
