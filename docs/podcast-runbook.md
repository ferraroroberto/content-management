# Podcast runbook: from recording to published

The ordered procedure for one episode, start to finish. The module reference
(stages, outputs, config) is [`podcast/README.md`](../podcast/README.md); the
recorder's is [`recorder/README.md`](../recorder/README.md). This page only
says what to do, in which order, and who does it. The `/podcast` skill
(`.claude/skills/podcast/SKILL.md`) drives these same steps with the owner in
a Claude Code session.

Each step is tagged:

- **[code]**: deterministic, no model involved.
- **[LLM: `role`]**: a hub call; the model is `podcast.models.<role>` in
  `config.json`.
- **[human]**: a decision or an action only the owner takes.

Times and costs are measured on the pilot: a 50-minute two-track interview,
15 clips, on this PC (NVENC render). Cost is the metered-API equivalent at list
prices; the hub runs on the subscription, so nothing is billed per call. The
pipeline itself never publishes, posts, schedules, emails or writes to Notion.

## At a glance

| # | Step | Who | Time (pilot) |
|---:|---|---|---|
| 1 | Prerequisites: config, hub, whisper | code + human | 1 min |
| 2 | Record (recorder) or import (Riverside) | human | the interview |
| 3 | Generate `episode.json`, fill the rest | code + human | 5 min |
| 4 | Run the stages, sync → render, episode, score | code + LLM | ~22 min |
| 5 | Review the clips, apply feedback, repeat | human + LLM | owner's pace |
| 6 | Covers and package | code | ~5 s |
| 7 | Check the package | human | 10 min |
| 8 | Publish (checklist) | human | — |

## 1. Prerequisites

1. **[code] Config.** `config/config.json` must carry the `podcast` block
   (copy it from `config/config_example.json`). The file is machine-local and
   untracked, so a fresh clone or a git worktree may not have it; the Podcast
   tab says so when the block is missing. Every path in it (`episodes_root`,
   `fonts.*`, `host.headshot`, `host.lightbulb`) must exist on disk.
2. **[code] The LLM hub** answers at `podcast.llm_hub_base_url` (default
   `http://127.0.0.1:8000`). Check:
   `curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/v1/models` → `200`.
   The hub is its own project, outside this repo; start it there if it is
   down.
3. **[code] The whisper server** answers at `podcast.whisper_url` (default
   port `8090`). Check: `curl -s http://127.0.0.1:8090/health` →
   `{"status":"ok"}`. Port 8090 is shared with other transcription tools, so
   one of them may hold it; it is started from the hub project too.
4. **[code] ffmpeg/ffprobe** on `PATH`. Set `podcast.video_encoder` to
   `h264_nvenc` on a machine with an NVIDIA GPU (libx264 works everywhere,
   slower).
5. **[human] Python invocation.** Always the project venv, with UTF-8 output:
   `$env:PYTHONUTF8 = "1"; & .\.venv\Scripts\python.exe ...`. Without
   `PYTHONUTF8` the logs' emojis crash a Windows console on cp1252.

## 2. Record or import the tracks

The pipeline needs **two video tracks, one per speaker**, in the episode
folder's `video editing/` subfolder. Create the episode folder under
`podcast.episodes_root` first.

**With the recorder** (preferred, see `recorder/README.md`):

1. **[human]** `& .\.venv\Scripts\python.exe recorder_server.py "<episode folder>"`.
   It prints a host link and a guest link.
2. **[human]** For a guest on another network: map an HTTPS Funnel entry to
   the recorder port and set `podcast.recorder.public_url`; a hosted TURN
   relay is needed for strict networks (mobile, corporate). Turn the Funnel
   entry off after the session.
3. **[human]** Both sides open their link in Chrome or Edge, pick camera and
   mic, choose 1080p, *Start recording*. **Clap once on camera at the start**
   — the manual sync fallback (step 4, troubleshooting) needs it.
4. **[code]** Chunks upload while recording; *Stop* finishes the file. Wait
   until the page says nothing is left to send. The files land as
   `video editing/recorder - <side> - <date time>.mp4`, plus the
   `remote-ref` files and `.json` sidecars the `sync` stage needs. Keep them
   all.

**With Riverside:** **[human]** download each speaker's separate video track
(not the mixed one) into `video editing/`. Riverside tracks are already
aligned, so the `sync` stage passes through.

## 3. Write `episode.json`

1. **[code]** Generate it from the tracks:

   ```powershell
   & .\.venv\Scripts\python.exe -m podcast.init_episode "<episode folder>" --guest "Full Name" --adjective brilliant --link "LinkedIn=https://..."
   ```

   It finds the recorder's main recordings or the Riverside per-speaker
   downloads in `video editing/` (the host's is the one named with
   `podcast.host.first`), and fills `tracks`, `date`, `start_s` 0, `end_s` at
   the shorter track, `website_slug` and `host_is_interviewee: false`. Two
   takes of one side stop it with the candidates named: pick with
   `--host-track` / `--guest-track`. It never overwrites an existing
   `episode.json` without `--force`. Seconds; no model.
2. **[human]** Fill what it leaves as `TODO` (listed at the end of its run),
   and check the rest. The schema is in
   [`podcast/README.md`](../podcast/README.md#the-episode-folder); the facts
   only the owner knows:
   - `guest`, `guest_display` (e.g. with a title), `guest_first`,
     `guest_pronoun_possessive` (`--pronoun`, default `their`).
   - `host_is_interviewee: true` when the guest interviews the owner (the copy
     switches to first person).
   - `start_s` / `end_s`: cut the pre-show and post-show chat. Find them by
     scrubbing the host track; the clips and the cleaned transcript only use
     this window.
   - `adjective`, `links` (the "where to find" list on the website page),
     `youtube_url` (empty until the episode is on YouTube).

The episode is private: `episode.json` lives next to the recording, never in
this repo.

## 4. Run the stages

From the 🎙️ podcast tab (pick the episode, keep all stages selected,
**▶ run episode pipeline**) or the CLI:

```powershell
& .\.venv\Scripts\python.exe podcast_pipeline.py "<episode folder>"
```

Stages run in order. Each one is skipped when its output exists, so a run
that dies (crash, power cut, a hub timeout) resumes where it stopped: run the
same command again. `--status` runs and writes nothing: it lists the tracks,
each stage as done / to run / waiting for review, the review summary and the
next stage (exit 2 when a track is missing). `--stages a,b --force` redoes chosen stages. `covers` and
`package` wait for the review (step 5), so the first run stops before them
with a "waiting for review" warning; that is expected.

| Stage | Who | Pilot time | Pilot cost | What to check after |
|---|---|---:|---:|---|
| `sync` | code | minutes (re-encodes the guest track) | — | recorder sessions only; `sync.json` offset and drift are logged |
| `transcribe` | code (whisper) | 2.5 min | — | `raw transcript.md` has no phrase repeated over and over |
| `clean` | LLM: `clean` | 5.5 min | n/a (unpriced backend) | the cleaned transcript reads well, both speakers labelled |
| `select` | LLM: `select` | 7.5 min | $0.30 | 15 clips in `clips.md`, one idea each, no overlaps |
| `copy` | LLM: `copy` | 1 min | $0.11 | titles lowercase, ≤5 words; LinkedIn hook + body + footer |
| `edit` | code + LLM: `caption_review` | 1.2 min | $0.14 | caption fixes and doubts per clip (shown in the tab) |
| `render` | code (ffmpeg) | 2–4 min | — | both crops play; captions on screen |
| `episode` | LLM: `episode_copy` | 0.5 min | $0.14 | YouTube title/description, website text |
| `score` | LLM: `score` (vision) | 2.5 min | $0.29 | per-clip 1–5 scores; low hooks flag clips to look at first |

Total on the pilot: about 25 minutes and $1 (priced stages), plus whatever
review rounds follow. `metrics.md` in the package holds the real table for
each episode, and the tab's **cost** view shows it.

Run long CLI stages with the log visible: in the tab the **log** expander
follows the run; on the CLI, keep the window open (a stage like `select` is
quiet for minutes while the model works).

## 5. Review the clips

**[human]** In the tab's **clips** view, for each clip: watch both crops,
read the cover (after step 6), the Instagram caption and the LinkedIn post,
the caption fixes and doubts, and the score. Then one of:

- **✅ approve**: ships as is.
- **🗑️ drop**: left out of the covers, the `.docx` and the website page.
- **✏️ request changes**: write the feedback in plain words, one note per
  problem, with the moment and the word when it is about captions or cuts:
  - `at 0:12 'happy' should be 'crappy'` (caption fix)
  - `start at 'sleep'` / `end after 'every night'` (trim)
  - `cut 'you know what I mean' at 0:31` (cut)
  - `start ten seconds earlier` (extend the source span; re-decodes the clip,
    about a minute)
  - `no punch-ins` / `stay on the guest from 0:20` (framing)
  - `title: why eight hours`, or a change to the Instagram caption or the
    LinkedIn hook/body (copy)

  Keep a caption-only note caption-only: the revise step changes only what the
  note names.

Outside the app, the same review is recorded from a terminal (it writes the
same `review.json`):

```powershell
& .\.venv\Scripts\python.exe -m podcast.review_cli "<episode folder>" show
& .\.venv\Scripts\python.exe -m podcast.review_cli "<episode folder>" approve 3 5
& .\.venv\Scripts\python.exe -m podcast.review_cli "<episode folder>" feedback 4 "at 0:12 'happy' should be 'crappy'"
```

**[LLM: `revise`] + [code]** **▶ apply feedback (N clips)** (or
`--stages revise`) maps each note onto edits, re-renders only those clips as
a new version (the old one stays viewable in the version picker) and puts them
back to pending. About 10 s per clip, a minute when a clip is extended.
Anything the model could not map is shown as **not applied**: rephrase it and
send again.

**Validate → fix → repeat:** re-watch each revised clip, then approve, drop,
or send another round, until every clip is approved or dropped. The tab says
"review done" when it is.

## 6. Covers and package

**[code]** Run again (tab, or the CLI command from step 4). With the review
done, `covers` and `package` run:

- the episode thumbnail and text card (`<guest> - <host> (1920x1080)_thumbnail.png`,
  `_text.png`) and one cover per kept clip in `clips/covers/`;
- `<guest> - <host>.docx`: YouTube copy, the clip list with copy and cut
  lengths, thumbnail texts, website text, links, and a thank-you note for the
  guest;
- `<guest> - <host> - website.html`: the episode page, paste-ready.

No LLM call; about 5 s. `--unreviewed` runs them before the review is done
(for a draft only).

## 7. Check the package

**[human]** In the tab's **files** view (or the `podcast package/` folder):

- The thumbnail and text card: names fit, nothing overlaps the brand mark.
- Each clip cover shows the clip's speaker, from inside the cut.
- The `.docx` and `clips.md` show each clip's **cut** length.
- The website HTML renders the "where to find" links from `episode.json`.
- A clip changed after the covers ran → re-run `--stages covers,package
  --force` (no LLM call).

## 8. Publish: manual checklist

The pipeline stops here. Everything below is the owner's, by hand:

- [ ] **YouTube:** upload the full episode with the title and description from
  the `.docx` and the episode thumbnail; put the URL in `episode.json`
  `youtube_url`, then re-run `--stages package --force` so the website page
  links it.
- [ ] **Website:** create the episode page, paste the website HTML into its
  text block, add the slug from `website_slug`.
- [ ] **Clips:** place the approved 1:1 / 9:16 files where the planning rows
  will point, then create one Notion clip page per clip with its title, copy,
  `clipPC` (folder) and `filePC` (file name). The weekly planning pipeline
  (`planning/videos/`, see its README) schedules them on LinkedIn, Instagram,
  X and Threads. A title with a `:` is fine: the scheduler falls back to the
  Windows-safe file name.
- [ ] **LinkedIn / Instagram copy:** take each clip's post and caption from
  `clips.md` (already in the clip page if pasted there).
- [ ] **Notion:** add the episode row and the clip rows (the pipeline only
  reads the clips table, for past titles and intros as style examples).
- [ ] **Guest:** send the thank-you note from the `.docx` with the links.

## Re-run an episode already edited by hand (trial)

To compare the pipeline's result with a package made by hand, run it in a
**separate trial folder**, so the existing package is never touched:

```powershell
& .\.venv\Scripts\python.exe -m podcast.init_episode "<trials root>\<episode name> (trial)" --from "<episode folder>"
```

**[code]** The trial folder gets its own `episode.json` that reads the source's
tracks by absolute path (the source's `episode.json` facts are copied when it
has one, else its tracks are discovered and the flags of step 3 apply).
Everything the pipeline writes then lands in the trial folder: the package,
`review.json`, the synced guest track and its own scratch folder (the scratch
is keyed by folder name, so the trial must be named differently). Nothing is
written beside the source. Set `podcast.trials_root` in `config.json` to the
trials' parent folder (outside OneDrive, the renders are large) and the
Podcast tab lists the trials after the episodes, so the review works there as
usual. Then follow steps 4–7 on the trial folder, or run `/podcast "<episode
folder>" --trial`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tab: "Missing 'podcast' block in config.json" | machine-local config without the block (fresh clone, worktree) | copy the block from `config/config_example.json` and fill the paths |
| A stage fails with a connection error | hub or whisper down | run the two checks in step 1, start the server in the hub project, re-run (the stage resumes) |
| `sync` stops: "no window cleared a peak ratio" | the reference audio is too quiet, too short or ambiguous to align | **clap fallback**: in a video editor, line up the clap on both tracks, export the shifted guest track into `video editing/`, point `tracks.guest` at it, then run every stage **except** `sync` (deselect it in the tab, or list the other stages in `--stages`) |
| `raw transcript.md` repeats a phrase many times | a whisper decoder loop that the loop repair missed | `--stages transcribe,clean --force`; if it persists, note the clip and fix captions in review |
| `clean` takes many minutes, or a chunk is rejected | slow model behind the `clean` role | it retries and caches chunk by chunk; keep `clean` on a fast model |
| `select` fails on the model's JSON | an unparseable reply | it retries once; re-run `--stages select` |
| A caption word is wrong | whisper mishears, more under cross-talk | feedback `at m:ss 'x' should be 'y'`, apply feedback |
| `covers`/`package` keep "waiting for review" | a clip is still pending or has changes | finish the review, or `--unreviewed` for a draft |
| `UnicodeEncodeError` in the log | console on cp1252 | `$env:PYTHONUTF8 = "1"` |
| A guest cannot connect to the call | no direct path on their network | configure the hosted TURN relay (recorder README) |
| LinkedIn reports FAIL for a scheduled clip video | the scheduler's confirmation came late | check LinkedIn's Scheduled list before re-running, or the post is duplicated |
