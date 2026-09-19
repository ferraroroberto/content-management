---
name: check-ip
description: Screen where my illustrations are being reused — pull a ranked batch of un-judged LinkedIn links from the check_ip store, open each in the browser, and propose a verdict (infringement / acceptable / unclear) with a reason and the poster's profile. Proposes only; never contacts anyone and never sets my decision. E.g. "/check-ip", "/check-ip 30", "/check-ip --image 'books pile - learn from mistakes.png'", "screen the next batch of IP matches".
---

# check-ip

**Goal:** work through the backlog of links where one of my illustrations turned up, and leave behind a proposed verdict for each so the final call is a quick yes/no instead of a cold investigation. The decision itself is never yours.

This is a **public repo** — never put a third party's name, profile URL, message thread, or case reference into an issue, PR, commit or any file you write. Those belong in the local store only.

## Boundaries — read these before the first batch

1. **Never contact anyone.** No connection request, no message, no comment, no reply. Not even a draft sent anywhere.
2. **Never file a report.** No LinkedIn copyright form, no takedown, no "report post".
3. **Never write `ok` / `person` / `chat` / `report` / `fixed`.** Those five columns are the owner's ten months of manual triage. `check_ip.screen` has no code path that writes them and a regression test enforces it — don't route around that with raw SQL.
4. **Never edit the database by hand.** Every write goes through `python -m check_ip.screen verdict`.
5. You are **proposing**, and your proposal is read as a proposal. Say "unclear" freely — a wrong confident verdict costs the owner more than an honest shrug.

## Arguments

`[N] [--image "<filename>"] [--source <platform>] [--recheck]`

- bare `/check-ip` → the configured batch size (default 20) of LinkedIn links.
- a number → that many links.
- `--image` → restrict to one illustration.
- `--source` → another platform (`Twitter/X`, `Instagram`, `Facebook`, `any`). Default is LinkedIn: that is where 97% of the owner's past decisions are and the only place acting on a finding is practical.
- `--recheck` → also serve rows already screened.

## Step 1 — pre-flight

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen stats
```

No store → say so and point at `& .\.venv\Scripts\python.exe -m check_ip.migrate`. Don't run the migration unasked.

Report the queue depth before starting, so the owner knows what fraction of the backlog this batch covers.

## Step 2 — pull the batch

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen next --limit <N> --json
```

Rows come ranked: the most-reused illustrations first, newest posts first. Each row gives `id`, `found_link`, `local_image`, `title`, `post_date` and how often that illustration has been seen on LinkedIn.

Run the whole batch **synchronously, to completion, in this turn**. Don't background it and end the turn — a sub-agent or headless session gets no wake-up and the batch would silently stop half-done.

## Step 3 — look at each link

Open `found_link` with the `mcp__claude-in-chrome__*` tools in the owner's real Chrome session (already logged in). Batch the tool load in **one** `ToolSearch` call:

```
select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp
```

This is third-party browser automation: present as a real human session, don't hammer, and close tabs as you go. If LinkedIn starts showing a captcha or an "unusual activity" notice, **stop the batch immediately** and report it — suspect a stealth regression, don't push through.

A post often 404s or sits behind a login wall. That is `unclear`, not a verdict — record it and move on.

## Step 4 — judge

You are answering one question: **is my illustration being used without credit?**

Lean `acceptable` when:
- it is the owner's own post, or a reshare of it that carries the original attribution
- the poster names Roberto Ferraro, tags the account, or links the original post
- the illustration is visibly credited in the image, caption, or first comment
- it is an aggregator that links back

Lean `infringement` when:
- the illustration is posted as the poster's own, with no credit anywhere
- a signature or watermark has been cropped out or painted over
- it is reused commercially — an ad, a paid course, a product page, a deck for sale
- the caption claims authorship ("I made", "my framework", "an illustration I drew")

Use `unclear` when the page won't load, the illustration isn't actually on it, credit might sit in a comment you can't see, or you simply can't tell. Prefer it to a guess.

Write the reason as one plain sentence a human can act on — *what* you saw, not a restatement of the verdict. "No credit anywhere in the post or the visible comments; caption presents the framework as the poster's own" beats "appears to be an infringement".

Capture the poster's profile URL — it turns the owner's follow-up into one click.

## Step 5 — record it

One call per row, straight after judging it (not batched at the end — a mid-batch stop should keep the work already done):

```powershell
& .\.venv\Scripts\python.exe -m check_ip.screen verdict --id <ID> --verdict <infringement|acceptable|unclear> --reason "<one sentence>" --poster-url "<profile URL>"
```

## Step 6 — hand back

Summarise in chat and stop:

- how many screened, split by verdict
- the ones you called `infringement`, each as one line: illustration → what you saw (no names in anything you *write to a file*; in chat is fine)
- remaining queue depth
- anything that looked off — repeated 404s, a login wall, a captcha

Then point the owner at the **⚖️ check IP** tab to make the actual calls. Do not offer to contact anyone, and do not offer to set the verdicts yourself.
