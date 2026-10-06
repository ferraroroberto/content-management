---
name: demo-video
description: Makes a product demo video with the owner — message brief, demo.json storyboard and a throwaway app driver, recording a disposable instance of the app, prep, hard-stop checks (overrun, privacy, licence), half-scale previews and final renders — and hands over the MP4s; it never publishes, posts or uploads. Use when the owner wants a demo video of one of their apps (any language, 16:9 / 1:1 / 4:5, public and private cuts), wants to change one, or asks where a demo stands. E.g. "/demo-video <demo folder>", "/demo-video new <app> <demo folder>", "/demo-video status <demo folder>".
---

# demo-video

**Goal:** take a demo from "I want a demo of app X" to verified MP4s the owner has reviewed: one `demo.json` storyboard, a throwaway driver, recordings of a disposable instance of the app, and one render per cut. Use the pipeline's own commands for everything they cover; judgment is only for what the owner would judge, and it is the owner's call wherever this skill says **[human]**.

The full procedure, with times and troubleshooting, is [`docs/demo-video-runbook.md`](../../../docs/demo-video-runbook.md); the storyboard schema and scene catalogue are in [`demo_video/README.md`](../../../demo_video/README.md); the driver how-to is [`docs/demo-video-driver-playbook.md`](../../../docs/demo-video-driver-playbook.md). Read the runbook once before step 0. This skill is that runbook, driven.

Every step is tagged **[code]** (a deterministic command), **[agent]** (your own reading and judgment) or **[human]** (the owner decides or acts; ask and wait).

## Hard rules

- **Never publish.** No posting, uploading, scheduling or emailing; the skill ends at the hand-over in step 7.
- **This repo is public.** Nothing about a private demo goes into the repo, an issue, a PR or a comment: no client, cohort or participant names, real decks, rosters, private paths or copyrighted music. The private demo lives in its own folder; only synthetic examples are committed.
- **Record only a disposable instance**, never the app the owner is running live. Before and after recording, note the live app's version endpoint and state-file mtimes and confirm they are unchanged.
- **A `check` that fails or is `unknown` stops the run.** Fix the cause; never render around it. `privacy.allow` is the owner's explicit decision only — never add a token to it yourself.
- **No recording or rendering before step 1's yes.** The message of every scene is confirmed in plain words first.
- **Run synchronously.** Record and render run for minutes: run them in the background with a log file and poll that log to completion inside the same turn (Monitor or an until-loop). Never end a turn waiting to be woken up.
- **Invocation:** the project venv with UTF-8 and unbuffered output, from the repo root: `PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe …`. Write any helper script to the scratchpad and run the file — never a heredoc (one silently mangled accented text). The driver runs the *app* from the app's own venv.

## Arguments

- `<demo folder>`: a demo in progress (it has `demo.json`). The default.
- `new <app> <demo folder>`: a new demo of `<app>` (a fleet repo name or checkout path) in a new folder outside the repo; start at step 1.
- `status <demo folder>`: run step 0 and `--status`, report, and stop.

## Progress checklist

Copy this into the conversation and tick it as you go:

```
- [ ] 0 preflight: node + template deps, ffmpeg, app checkout + venv, demo folder      [code]
- [ ] 1 message brief + per-scene sentences confirmed                                  [human]
- [ ] 2 demo.json + driver.py drafted, scene table approved                            [agent → human]
- [ ] 3 probe take, then record + recording contact sheets reviewed                    [code → agent]
- [ ] 4 prep: transcode, states measured, music read                                   [code]
- [ ] 5 checks all pass                                                                [code]
- [ ] 6 preview + contact sheet per cut, review loop until the owner is happy          [code → human]
- [ ] 7 final renders verified, MP4s + before-you-share notes handed over             [code]
```

## 0. Preflight [code]

Run and report in one block; fix and re-check until all pass:

1. `node --version` (20+). `demo_video/remotion/node_modules` exists — if not, `npm install --no-audit --no-fund` there (a fresh checkout or worktree has none).
2. `ffmpeg -version` and `ffprobe -version` resolve.
3. The app's checkout and its `.venv` interpreter exist (ask for the path if it isn't a sibling under `E:\automation`).
4. The demo folder is outside this repo. With `demo.json` present: `demo_video_pipeline.py "<folder>" --status` — read it out.

## 1. The message brief [human]

Ask in **one message**: who watches it and where; the cuts (id, language, aspect, public or private); target length; the music and its licence (copyrighted → `private-only`, only in private cuts); the app's items or screens worth showing; for a private cut on real material, where the real roster is. Then propose a scene list with **one plain sentence per scene saying its message** (what the viewer should understand), echo it back, and wait for an explicit yes. Change it until the owner says yes.

## 2. Storyboard and driver [agent → human]

- Copy the worked example (`demo_video/examples/facilitation-suite/`) into the demo folder as the starting point, or start from scratch for another app.
- Write `demo.json`: scenes from the catalogue matching the confirmed sentences; every on-screen string and every chat answer in `copy` per language; clips with `source: "rec/{lang}/<page>.webm"`; the soundtrack per cut with `licence`; `privacy` (roster file in the demo folder, blocklist of client names for public cuts); the `recording` takes and beats.
- Write `driver.py` from the playbook: a disposable instance, synthetic data for public cuts, an anonymised copy (names, roles, companies, countries) for private ones, `act` raising on refusal.
- Show the owner a table — scene · seconds · what it shows · its message sentence · caption text per language — and wait for approval. Validate with `--status` (it lists missing copy keys, clips and beats).

## 3. Record [code → agent]

1. **Probe first** on a new app or driver: a copy of `demo.json` with one take and one short beat, `--stages record`, then look at one frame of each `.webm` (`ffmpeg -ss <t> -i <webm> -frames:v 1 <png>`). Fix the driver before the full run.
2. Note the live app's version endpoint and state-file mtimes.
3. `demo_video_pipeline.py "<folder>" --stages record,prep` in the background with a log; poll to completion.
4. Check every `.webm` with `ffprobe` against the marks (a short video is the screencast symptom — see the runbook), confirm the live app is unchanged, and read `out/prep/<lang>-<clip>.png`: each beat must show what its scene needs (right item, answers on screen, timer states). Re-record only what's wrong.

## 4. Prep [code]

Runs with step 3; alone it is `--stages prep`. Report the measured state timelines and the music entry points from `out/prep/music-<cut>.json`; if a closing track's best entry differs from the storyboard's crossfade scene, propose the change.

## 5. Checks [code]

`--stages check`. All `pass` → continue. Otherwise report each `fail`/`unknown` line and fix the cause: overruns by `rate`/`offset`/`seconds` (or a longer beat hold and a re-record); privacy by changing the made-up name (only the owner may extend `privacy.allow`); licence by changing the track or making the cut private.

## 6. Preview and review loop [code → human]

For each cut: `--cut <id> --preview` (background + poll), then look at `out/<cut>.preview.sheet.png` yourself before showing it. Send the preview to the owner (`SendUserFile` where the session can) with a short list of what changed. Apply feedback to `copy`/`scenes`/`soundtrack` and preview again with `--force`; re-record only when a beat itself must change. Loop until the owner approves each cut.

## 7. Final render and hand-over [code]

`demo_video_pipeline.py "<folder>" --force` (background + poll). Every cut must log `✅ output … pass`; a `fail`/`unknown` verification is reported, never shipped as done. Hand over the MP4 paths (send them where the session can) with the **before-you-share notes**: which cuts carry private-only music and must never be posted publicly, and anything personal or client-specific visible in a private cut.

## When something fails

Read `docs/demo-video-runbook.md` → Troubleshooting first. Report the exact error line; never retry a refused action blindly (it usually means the beat targets the wrong item), never delete or edit the owner's files outside the demo folder, and never touch the live app.
