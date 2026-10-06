# Demo-video runbook

How to make a product demo video, from "I want a demo of app X" to the MP4s: a 1–2 minute, 16:9 (plus 1:1 / 4:5 if wanted), animated, with music, in one or more languages, with a public and a private cut when needed. The interactive way is the **`/demo-video` skill** in Claude Code, run from this repo; it follows these steps with you. The control panel's 🎬 demo video section runs the deterministic stages (prep, check, preview, render) and shows this page.

Nothing here publishes, posts or uploads anything.

## At a glance

| Step | What | Who | Time |
|---|---|---|---|
| 0 | Preflight: node + `npm install`, ffmpeg, the app's checkout and venv, a demo folder outside the repo | code | 2 min |
| 1 | **Message brief**: audience, cuts, languages, length, aspects, music + licence, and **the message of each scene in one plain sentence** | you | 10 min |
| 2 | Storyboard (`demo.json`) + throwaway driver (`driver.py`) | agent, then you approve | 20–40 min |
| 3 | Record, then look at the recordings' contact sheets | code, then agent | ~3 min per language |
| 4 | Prep: transcode, measure state timelines, read the music | code | 1 min |
| 5 | Checks: overrun, privacy, licence — a hard stop | code | seconds |
| 6 | Half-scale preview + contact sheet per cut, loop on wording and timing | code, then you | ~1¾ min per cut |
| 7 | Final render, verification, delivery + "before you share" notes | code | ~2½ min per 83 s cut |

No model calls are needed: the only cost is the agent session.

## 0. Preflight

- `node --version` (20+) and `npm install` once in `demo_video/remotion/` (its `node_modules` never goes under OneDrive).
- `ffmpeg -version`, `ffprobe -version`.
- The app to film: its checkout and its `.venv` (the driver runs the app from there).
- A **demo folder outside the repo** (a private one may sit in OneDrive; the repo is public).

## 1. The message brief

Agree, in one message, before anything is recorded:

- who watches it, and where (a client email, a LinkedIn post, a talk);
- the cuts: id, language, aspect, **public or private**;
- the length (60–120 s works) and the music — and its licence: a copyrighted track is `private-only` and the licence check refuses it in a public cut;
- **the message of each scene, one plain sentence each**, echoed back and confirmed.

The last point is the one that matters: the reference video shipped a scene saying "cameras off, a silent chat" when the real message was "everyone is on camera and joins in through the chat, with no other app" — fixed only after the final render. A sentence per scene, read back before recording, catches that in a minute.

## 2. Storyboard and driver

- **`demo.json`** (schema and scene catalogue: [`demo_video/README.md`](../demo_video/README.md)): `product`, `clips` with their `source` recordings, `copy` per language (every on-screen string and every chat answer), `cuts`, `scenes` (each scene type from the catalogue, with `seconds`, captions as `@copy` keys and clip refs to beats), the `soundtrack` per cut, `privacy` (roster, blocklist, accepted tokens) and `recording` (takes, pages, beats). Start from [`demo_video/examples/facilitation-suite/demo.json`](../demo_video/examples/facilitation-suite/demo.json).
- **`driver.py`**: a throwaway driver for the app, written from [`docs/demo-video-driver-playbook.md`](demo-video-driver-playbook.md) — a disposable instance, synthetic data for a public cut, an anonymised copy for a private one, actions that raise when refused.
- **Private cut on real material**: put the real roster in `privacy.roster` (a file in the demo folder, never in the repo).

## 3. Record

```powershell
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --stages record,prep
```

`record` boots the app through the driver, plays every take in real Chrome and writes `rec/<lang>/<page>.webm` plus the marks file. Do a 20-second probe take first on a new app. Then open `out/prep/<lang>-<clip>.png` — one frame per beat — and confirm every beat shows what the brief says (the right item, answers on screen, the timer in the expected state).

## 4. Prep

Runs with `record` above, or alone (`--stages prep`): transcodes the recordings to CFR H.264 in `media/`, measures any `legend.measure` state timeline into the marks file, writes `out/prep/music-<cut>.json` (loudness per second and, for a closing track, the scenes where it builds best) and the recording contact sheets.

## 5. Checks

```powershell
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --stages check
```

`overrun` (no sped-up clip runs past its beat), `privacy` (no real name or name token; no blocklisted term in a public cut) and `licence` (no private-only track in a public cut). `fail` or `unknown` stops the run (exit 3, report in `out/prep/checks.json`); `render` always runs them first. Fix overruns with a scene's `rate`/`offset` or a beat's hold; fix privacy by changing the made-up name, or — your call only — add a common first name to `privacy.allow`.

## 6. Preview and review

```powershell
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --cut <id> --preview
```

Half scale, about 1¾ min per 83 s cut, plus `out/<cut>.preview.sheet.png` with one frame per scene. Review the sheet and the preview; change wording in `copy`, timing in `scenes` (`seconds`, `rate`, `offset`), music in `soundtrack`, and preview again. Only re-record when a beat itself must change.

## 7. Final render and delivery

```powershell
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --force
```

Every cut at 1080p (about 2½ min per 83 s cut), each verified — size, fps, length ±1 s, an audio track, a fade at the end — with a per-scene contact sheet. Hand over the MP4s with the **before-you-share notes**: which cuts carry private-only music (never public), and what the private cut shows on screen (the real deck, the owner's own photo, …).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A page's video is a second or two long | Chrome stopped sending screencast frames for a page with no Playwright traffic | the harness's keep-alive handles it; if it recurs, check `ffprobe` durations against the marks first |
| `action … refused` during record | the script targets the wrong item (live numbering skips excluded items) or an action the item doesn't support | navigate by id or include every item (the example driver's `include_all`); read the item's own settings |
| A timer legend shows the wrong state | the item's timer starts on entering, fighting a scripted start/pause | `start: manual` for scripted timers; measure states with `legend.measure` |
| `overrun: fail` | a sped-up clip runs into the next beat | lower `rate`, reduce `seconds`, or hold the beat longer when recording |
| `privacy: fail` on a first name | a made-up name shares a token with a real one | change the made-up name; `privacy.allow` only as the owner's explicit decision |
| `Remotion is not installed in …` | a fresh checkout or worktree | `npm install` in `demo_video/remotion/` |
| Renders are slow to start | Remotion copies `media_dir` into its bundle | keep only what the demo uses in `media/` |
| `rmtree` "Access is denied" on a OneDrive copy | read-only attributes on copied files | `copytree(..., copy_function=shutil.copyfile)`, `rmtree(..., onexc=chmod-and-retry)` |
| A heredoc edit silently broke accented text | stdin encoding | write helper scripts to a file and run them with `PYTHONUTF8=1` |
