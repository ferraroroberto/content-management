---
name: podcast
description: Drives one podcast episode from its recorded tracks to a reviewed, local episode package with the owner — preflight, episode.json, the pipeline stages, the clip-by-clip review loop, covers and package — then stops at a manual publishing checklist; it never publishes, posts, uploads, schedules or emails. Can work in a separate trial folder so an episode already edited by hand is re-run without touching its package. Use when the owner wants to process a new episode, resume one, re-run the pipeline on an old episode for comparison, or check where an episode stands. E.g. "/podcast <episode folder>", "/podcast <episode folder> --trial", "/podcast status <episode folder>".
---

# podcast

**Goal:** take one episode from its two recorded tracks to a package the owner has reviewed clip by clip: transcript, 15 clips in 1:1 and 9:16 with captions and copy, covers, the episode `.docx` and the website page. Use the pipeline's own commands for everything they cover. Judgment is only for what the owner would judge, and it is the owner's call wherever this skill says **[human]**.

The full procedure, with times, costs and troubleshooting, is [`docs/podcast-runbook.md`](../../../docs/podcast-runbook.md). Read it once before the first step. This skill is that runbook, driven.

Every step is tagged **[code]** (a deterministic command), **[LLM: role]** (a hub call made by the pipeline, the model is `podcast.models.<role>`), **[agent]** (your own reading and judgment) or **[human]** (the owner decides or acts; ask and wait).

## Hard rules

- **Never publish.** No posting, uploading, scheduling, emailing, no Notion writes, no `planning_pipeline.py`. The skill ends at the manual checklist in step 8.
- **Trial mode never touches the source folder.** All writes go to the trial folder. Before and after the run, compare a listing of the source (step 1b).
- **This repo is public.** Nothing about an episode goes into the repo, an issue, a PR or a comment: no guest names, transcript text, clip titles or paths. The episode facts live only in the episode folder.
- **Don't start, stop or restart** the LLM hub, the whisper server or the control panel. If one is down, say so and stop. The owner owns them.
- **`--force` only with a reason.** Before forcing a stage, say what it redoes and whether it calls a model. Never force a stage whose output the owner has already reviewed unless they ask.
- **Run synchronously.** Long stages run in the background with a log file. Poll that log to completion inside the same turn (Monitor or an until-loop on the log). Never end a turn waiting to be woken up.
- **Invocation:** always the project venv with UTF-8 and unbuffered output, from the repo root:
  `PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe …`. Bare `python` is blocked, and without `PYTHONUTF8` the emoji logs crash on a cp1252 console. Write any helper script to the scratchpad and run the file. Never use a heredoc, and never name a scratch file after a stdlib module: a scratch `inspect.py` once broke an import.

## Arguments

- `<episode folder>`: a new or in-progress episode. The default.
- `<episode folder> --trial [<trial folder>]`: re-run an episode the owner already edited by hand into a **separate trial folder**. The default trial folder is `<podcast.trials_root>/<episode name> (trial)`. If `trials_root` is not set, ask where to put it, outside OneDrive. The trial is listed in the Podcast tab only when it sits under `trials_root`.
- `status <episode folder>`: run step 0 and `--status`, report, and stop.

## Progress checklist

Copy this into the conversation and tick it as you go:

```
- [ ] 0 preflight: config, hub, whisper, ffmpeg            [code]
- [ ] 1 episode.json (or trial folder) + --status green      [code + human]
- [ ] 2 sync + transcribe, loop check                        [code]
- [ ] 3 window: start_s / end_s confirmed                    [agent + human]
- [ ] 4 clean, select, copy, edit — clip list + caption doubts shown [LLM]
- [ ] 5 render, episode copy, score — files verified         [code + LLM]
- [ ] 6 review loop until every clip approved or dropped     [human + LLM: revise]
- [ ] 7 covers + package — package checked                   [code + human]
- [ ] 8 cost table + manual publishing checklist handed over [agent]
```

## 0. Preflight [code]

Run all four checks, report them in one block, and fix and re-check until they all pass:

1. Config: `./.venv/Scripts/python.exe -c "from podcast.episode import load_podcast_config as c; p=c(); print(p['models'], p.get('trials_root'))"`. If it says *Missing 'podcast' block*, `config/config.json` is machine-local and this checkout lacks it, which is common in a git worktree or a fresh clone. Stop, and ask the owner to copy the block from the primary checkout or from `config/config_example.json`. Every font and host asset path in it must exist.
2. Hub: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/v1/models` → `200`.
3. Whisper: `curl -s http://127.0.0.1:8090/health` → `{"status":"ok"}`.
4. `ffmpeg -version` and `ffprobe -version` both resolve.

If the hub or whisper is down, report it and stop. They are started from the hub project, not here.

## 1. The episode folder [code + human]

**1a. New episode.** If the folder has no `episode.json`, ask the owner in **one message** for the guest's full name, the display name (with a title, if any), the pronoun, the adjective ("my conversation with the ___") and the "where to find" links. Also ask whether the guest interviewed the owner (`host_is_interviewee`). Then run:

```
./.venv/Scripts/python.exe -m podcast.init_episode "<folder>" --guest "<name>" --guest-display "<display>" --pronoun <her|his|their> --adjective <adjective> --link "LinkedIn=<url>"
```

- **[code]** It finds the recorder's main recordings or the Riverside per-speaker downloads in `video editing/`, and fills the tracks, `date`, `start_s` 0, `end_s`, `website_slug` and `host_is_interviewee: false`.
- If it stops with "N host tracks…", there are two takes of one side. Show the owner the candidates and re-run with `--host-track` / `--guest-track` **[human]**.
- Exit 1 means an `episode.json` already exists. That is fine: use it. Never pass `--force` without the owner's OK.
- Edit `host_is_interviewee` in the file if the owner said so. Anything still `TODO` is listed at the end of the run. Get it from the owner, then edit the file.

**1b. Trial of a hand-edited episode.** Create the trial and **snapshot the source**:

```
./.venv/Scripts/python.exe -m podcast.init_episode "<trial folder>" --from "<source folder>"
find "<source folder>" -type f -printf "%P %s %T@\n" | sort > "<scratchpad>/source-before.txt"
```

The trial reads the source's tracks by absolute path. If the source has an `episode.json`, it is copied with its facts. Otherwise the tracks are discovered, and the flags from 1a apply. Every output then lands in the trial folder: the package, `review.json`, the synced track and its own scratch folder. Nothing is written beside the source.

**Validate (both cases) [code]:** `./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --status` must exit 0, with both tracks found. It writes nothing. Exit 2 means a missing track: fix `tracks`, then re-check. On a resumed episode, `--status` also tells you where to pick up. Go to the step that owns the first stage marked "to run".

## 2. Sync and transcribe [code]

```
PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --stages sync,transcribe > "<scratchpad>/run-a.log" 2>&1
```

This takes a few minutes for a 50-minute episode; run it in the background and poll the log for `✅ metrics`, `❌` or `Traceback`.

- `sync` is a no-op for Riverside. For a recorder session, the log gives the offset and drift. If it stops with "no window cleared a peak ratio", follow the runbook's clap fallback. That is a **[human]** step in a video editor. Afterwards run every stage except `sync`.
- **Validate:** read the head and tail of `<package>/transcript/raw transcript.md`, and scan it for one phrase repeated many times (a whisper loop). If you find one, re-run `--stages transcribe --force` once. If it persists, note the time and carry on: caption errors are fixed in the review.

## 3. The window [agent + human]

The transcript covers the whole recording. Read its first and last few minutes. Propose `start_s` where the interview proper begins (after the greetings and setup) and `end_s` where it ends. Quote the timestamp and the first words, and don't make clip choices here. The owner confirms or corrects. Write both values into `episode.json` **before** `clean`: `clean` and `select` only see this window.

## 4. Clean, select, copy, edit [LLM: clean, select, copy, caption_review]

```
PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --stages clean,select,copy,edit > "<scratchpad>/run-b.log" 2>&1
```

On the pilot this took about 15 minutes: `select` is quiet for about 7 minutes while the model works. If `select` fails on the model's JSON, re-run `--stages select` once.

**Validate before rendering:**
- The cleaned transcript reads well, with both speakers labelled.
- `clips.md` has 15 clips, one idea each, with no overlaps. Titles are lowercase and at most 5 words, and when `host_is_interviewee` is set, the copy is in the first person.
- Per clip, `clips.json` → `caption_review` lists the fixes applied and the doubts.

Show the owner a compact table: number, title, from, length, caption doubts. Don't render clips the owner has already rejected. Drop them now with `review_cli drop`, see step 6.

## 5. Render, episode copy, score [code + LLM: episode_copy, score]

```
PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --stages render,episode,score > "<scratchpad>/run-c.log" 2>&1
```

The first full run stops before `covers` and `package` with "waiting for review". That is expected.

**Validate:** `--status` shows `render`, `episode` and `score` done. For each kept clip, both `clips/1x1/` and `clips/9x16/` files exist, and their ffprobe durations match the clip's `edit.cut_s` in `clips.json` within a second. A missing file → re-run `--stages render` (it resumes). Read `scores.json`: clips with hook ≤ 2 or self-contained ≤ 2 go first in the review.

## 6. Review loop [human + LLM: revise]

The owner reviews; you record and apply. The owner watches the clips either in the Podcast tab (the episode, or a trial under `trials_root`) or straight from the files `review_cli show` lists. Show the review state at any time with:

```
./.venv/Scripts/python.exe -m podcast.review_cli "<folder>" show
```

Walk the clips in the order the owner prefers. Lowest score first is a good default. For each clip, give its title, length, score, caption doubts and the two file paths, then ask: **approve, drop, or changes?** Record what the owner says:

```
./.venv/Scripts/python.exe -m podcast.review_cli "<folder>" approve 3 5
./.venv/Scripts/python.exe -m podcast.review_cli "<folder>" drop 7
./.venv/Scripts/python.exe -m podcast.review_cli "<folder>" feedback 4 "at 0:12 'happy' should be 'crappy'"
```

How to phrase feedback: write one note per problem, in the owner's words, and give the moment and the word when the note is about captions or cuts. The notes the revise step understands:
- caption fixes: `at m:ss 'x' should be 'y'`
- a new start or end word: `start at 'sleep'`, `end after 'every night'`
- cuts: `cut 'you know' at 0:31`
- extending the clip: `start ten seconds earlier`, which re-decodes the clip in about a minute
- framing: `no punch-ins`, `stay on the guest from 0:20`
- a new title: `title: why eight hours`
- the Instagram caption or the LinkedIn hook or body

**A caption-only note stays caption-only.** The owner expects cuts, framing and copy to be left alone.

Then apply the feedback:

```
PYTHONUTF8=1 ./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --stages revise
```

About 10 s per clip. Only clips with open feedback are re-rendered, as a new version, and the old version is kept.

**Validate → fix → repeat:** run `review_cli show` again.
- Each revised clip is back to `pending` at the next version.
- Anything listed under "not applied" was not mapped. Rephrase it more concretely with the owner and send it again.
- The owner re-watches each revised clip, then approves, drops or sends another round.

Loop until `show` reports every clip approved or dropped, with at least one approved. Some calls are only the owner's, never yours: whether to keep a passage where someone names a private person, a health detail, or anything the guest might not want public. Flag any you notice, then ask.

## 7. Covers and package [code + human]

```
PYTHONUTF8=1 ./.venv/Scripts/python.exe podcast_pipeline.py "<folder>" --stages covers,package
```

No model call; it takes seconds.

**Validate:**
- **[agent]** `--status` shows every stage done. The `.docx` and `clips.md` give each clip's **cut** length. There is one cover per kept clip, with none for dropped clips. The website HTML lists the `links` from `episode.json`.
- **[human]** The owner looks at the thumbnail and text card: names fit, nothing overlaps the brand mark. They also check that each clip cover shows that clip's speaker.

If a clip changes after this step, re-run `--stages covers,package --force`. It makes no model call.

**Trial mode:** re-list the source and `diff` it against `source-before.txt`. It must be identical. If it is not, stop and report: that is a bug, not an expected outcome. Then give the owner the two package folders side by side to compare.

## 8. Hand-over [agent]

Report:
- the cost table from `<package>/metrics.md`: wall time per stage and the metered-equivalent cost;
- the review outcome: kept, dropped, rounds, and anything never applied;
- the package's paths;
- the manual publishing checklist, verbatim from the runbook's step 8: YouTube, the website page, clip scheduling, the LinkedIn and Instagram copy, the Notion rows and the guest's thank-you note.

These steps are the owner's. Offer no automation for them, and stop.

## When something fails

Match the error against the runbook's troubleshooting table first. Those failures recurred on the pilot:
- the missing config block;
- the hub or whisper down;
- the sync peak ratio;
- whisper loops;
- slow `clean`;
- `select` JSON;
- "waiting for review";
- cp1252 crashes.

A crashed or interrupted run resumes: run the same command again, since each stage skips the output it already has. Never delete outputs to "start clean" without the owner's OK.

If a stage raises a Python error that is not in the table, stop and report it with the log tail. It is a pipeline bug and goes to an issue, not a workaround here.
