# Demo-video driver playbook

How to write the **throwaway driver** that lets `demo_video_pipeline.py --stages record` film any app for a demo video. A driver is cheap and disposable: it lives in the demo folder next to `demo.json`, it is written for one demo, and nothing is ever added to the app's own repo. The worked example is [`demo_video/examples/facilitation-suite/driver.py`](../demo_video/examples/facilitation-suite/driver.py); start from a copy of it.

Budget: about 15 minutes for an app with an HTTP action API, longer for one you can only click through.

## What the harness does, and what the driver does

The harness (`demo_video/record.py`) owns:
- the browser (real Chrome, a fixed 1920×1080 viewport);
- one browser context per page (one video file per page: `rec/<lang>/<page>.webm`);
- the beat runner and the chat pacing;
- the marks file: `[start, end]` of each beat per page video, measured from that page's own creation.

The driver is a Python file defining `make_driver(args) -> driver`, where `args` is `recording.driver_args` from `demo.json`:

| Method | Must |
|---|---|
| `boot(workdir, lang) -> dict` | Start a **disposable** instance of the app under `workdir` (temp, deleted afterwards) and return `{"base_url": ..., <placeholders>}`. The placeholders fill `{name}` in page URLs and init scripts, e.g. `{session}`. |
| `stop()` | Stop that instance. The harness calls it even when a step fails. |
| `act(name, arg)` | Perform one app action, and **raise** when the app refuses it. |
| `chat(sender, text)` | *(optional)* Make one message appear as if a participant typed it. |
| `tick()` | *(optional)* Called about once a second during waits, e.g. to keep a "connected" indicator green. |

The **beats** are data in `demo.json` (`recording.takes[].beats[].steps`), not driver code:
- **App steps:** `act`/`arg` and `chat` (a list, or an `@copy` key so each language has its own answers).
- **Timing:** `wait`.
- **Browser steps on a named page:** `click` (optionally the row with `text`), `wait_for`, `wheel`, `mouse`.

See `demo_video/README.md` for the full schema.

## Write one, step by step

1. **Find how the app starts in isolation.** Look in the app's e2e conftest first. Most fleet apps have a `boot_instance`-style helper that starts the server on a free port, with config, data and secrets paths pointed at temp files through env vars (facilitation-suite: `FS_CONFIG_PATH`, `FS_LEDGER_PATH`, `FS_DATA_DIR`, `FS_ENV_PATH`). Copy what it does into `boot()`, running the app's **own venv** (`<repo>/.venv/Scripts/python.exe`) with `cwd=<repo>` and `creationflags=CREATE_NO_WINDOW`. Don't import the app's code into this repo's process. Poll a health endpoint with a hard timeout and fail with the server log's path.
2. **Find its control surface.** In order of preference:
   - an HTTP action API (facilitation-suite: `POST /api/actions/{id}[/{arg}]`, the same intents its keyboard and Stream Deck send);
   - keyboard shortcuts, as `click`/browser steps;
   - plain Playwright clicks.

   Whatever it is, `act` raises on a refusal. A logged warning is how the reference run recorded ten beats of the wrong items without noticing.
3. **Decide the data.**
   - **Public cut:** the app's own synthetic fixture only (facilitation-suite: `python -m tests.fixtures.demo --root … --ledger …`).
   - **Private cut on real material:** an *anonymised copy*, made by the driver in `boot()` and never written back.
     - Swap names, roles, companies and countries from fixed made-up lists. Anonymise everything a screen can show: the reference video's Groups tab showed real job titles and countries after the names were already swapped.
     - Put the real roster in `privacy.roster`, so the `check` stage proves no made-up name shares a token with a real one.
4. **Make the data film well.** Patch the copied plan or settings, not the app:
   - shorten a timer so a clock visibly runs out;
   - turn music off (the soundtrack is added at render);
   - include every item you navigate to (see the lessons below).
5. **Write the beats** in `demo.json`. A beat is one moment the video will show; name it the way the scenes will refer to it. Add a short `wait` after each navigation so the screen settles. Hold about 3 s after the last chat answer.
6. **Do a probe first.** Run one short take (one beat, about 20 s) and look at a single frame (`frame_at`) before the full run. A wrong URL, an unexpected empty state or an unreadable font is a 20-second lesson instead of a 4-minute one.
7. **Record, then run prep and check.** `--stages record,prep,check`. Read the recordings' contact sheets in `out/prep/`, and fix overruns by changing a scene's `rate`/`offset` or a beat's hold.

## Lessons (each one cost a re-run)

- **Never the live instance.** Boot a disposable one, every time. Afterwards, prove the live app was untouched: compare its state files' mtimes and its version endpoint before and after.
- **Navigate by id, or make positions stable.** facilitation-suite's live numbering skips items excluded from the session, so every `goto N` after a skipped item hit the wrong one. The example driver's `include_all` makes the numbers match the plan. Better, where the app allows it, navigate by id.
- **Read an item's own settings before scripting it.** A timer set to start when the item opens turned a scripted start → pause → resume into pause → resume → pause. Set `start: manual` on items the script drives (`timers` in the example's `driver_args`), and let `prep` *measure* state changes rather than assuming them.
- **Workspaces copied from OneDrive carry the read-only attribute.** `shutil.copytree(..., copy_function=shutil.copyfile)`; `shutil.rmtree(..., onexc=<chmod and retry>)`.
- **Run Python from files with `PYTHONUTF8=1`,** not heredocs on stdin: a heredoc mangled accented characters and silently broke an edit. Use the app's own venv (a hook blocks bare `python`).
- **Post chat the way the real source does.** facilitation-suite tags simulator messages with a "sim" chip; the example posts as the Zoom reader (`source: "zoom"`) plus a heartbeat in `tick()`, so the presenter screen shows a live reader.
- **Two languages can record in parallel,** on separate instances and free ports. Check each run's log separately: `tail -n` over two files in one command made a background task report `failed` when both recordings had succeeded.
- **Playwright starts a page's video when the page is created** and finishes it on `context.close()`. That is why marks are per page, and why each page role needs its own context.
- **A page with no Playwright traffic stops getting video frames.**
  - **What was measured:** with two app pages open, the presenter page recorded 1.1 s of an 8 s take while its DOM kept changing. This happened in real Chrome and in bundled Chromium, with Playwright 1.61 and 1.63, in one browser or two, and the anti-occlusion flags didn't help. The original recorder only worked because it happened to post its chat through the presenter page's own `page.request`.
  - **What the harness does:** `Runner.keepalive` evaluates `0` on every page every quarter second during waits, so drivers can use plain HTTP (`requests`) for their actions. If a video still comes out short, check `ffprobe` durations against the marks before anything else.

## The facilitation-suite example, briefly

`driver_args`:

```json
{"repo": "<path to a facilitation-suite checkout>", "include_all": true, "shuffle_seed": 7,
 "timers": {"11": {"seconds": 14, "end": "keep", "start": "manual"}}}
```

`boot()` does the following:
1. Builds the synthetic session.
2. Drops `include: false`.
3. Shortens the coffee-break timer.
4. Writes a config from the app's `config.sample.json` with OBS, the chat reader and the quiz listener off.
5. Starts uvicorn on a free port.
6. Shuffles the groups.
7. Returns `{"base_url", "session"}`.

The three takes record:
- the app's Sessions → Plan → Groups tour;
- the live run, presenter and stage together, over 10 beats;
- the Results tab.
