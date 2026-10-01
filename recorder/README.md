# recorder/ — self-hosted two-side podcast recorder

Each participant opens their own link, records their camera and mic **locally
at full resolution**, and the page uploads the recording in chunks while it
runs. The finished file lands in the episode's `video editing/` folder, which
is where `podcast_pipeline.py` reads its tracks from. This replaces the one
job Riverside did for the podcast. The design and the probes behind it are in
#336; this is build step 1 of 3 (#341). The in-page call (#342) and the track
sync (#343) come next.

## Run

```powershell
& .\.venv\Scripts\python.exe recorder_server.py "<episode folder>"
& .\.venv\Scripts\python.exe recorder_server.py "<folder name under podcast.episodes_root>" --port 8470
```

It prints two links, one for the **host** side and one for the **guest**
side, and serves until stopped (Ctrl+C). The links are random tokens kept in
`<episode folder>/recorder.json`, so a restart keeps the links already sent.
A request with an unknown token gets a 404.

Each side opens its link in **Chrome or Edge**, picks camera and microphone,
chooses 1080p (default) or 4K, and presses *Start recording*. *Stop* ends it.
The page shows how much is uploaded. The file is
`video editing/recorder - <side> - <date time>.mp4`.

## Reaching it from outside this machine

The camera needs a secure context: `http://127.0.0.1` works on this machine,
but a guest elsewhere needs an **HTTPS** URL. Map one, for this port only,
with a Tailscale Funnel entry (a machine-local step, not in this repo). Funnel
serves on HTTPS ports 443, 8443 or 10000; pick one that `tailscale serve
status` does not already use:

```powershell
tailscale funnel --bg --https=<port> http://127.0.0.1:8470
```

Then set `podcast.recorder.public_url` in `config.json` to the HTTPS URL
Funnel prints, so the printed links use it. Turn the entry off after the
session (`tailscale funnel --https=<port> off`). The link token is the only
credential, so share each link only with its participant.

## How it works

- **Capture:** `getUserMedia` at the chosen resolution (30 fps), recorded with
  `MediaRecorder` as H.264 + Opus in WebM at 12 Mbps (1080p) or 40 Mbps (4K),
  in 1 s chunks. **Not MP4:** Chrome's MP4 recorder crashes the tab once one
  recording passes 4 GiB of output (reproduced at 2³² bytes: ~46 min at 1080p,
  ~14 min at 4K); its WebM muxer has no such limit. MP4 is only the fallback
  for a browser that cannot record WebM. Echo cancellation stays on (the call
  in #342 needs it); noise suppression and auto gain are off.
- **No loss on a crash or a dropped link:** every chunk goes to IndexedDB as
  it arrives and is deleted only once the server has acknowledged it, so tab
  memory stays flat however long the recording runs. A single upload loop
  sends chunks oldest first and retries with backoff (up to 5 s). After a
  failure it asks the server which chunk numbers it already holds
  (`/have`), so nothing is sent twice. A reload resumes the upload, and a
  recording the tab never stopped (a crash, a closed tab) is closed at the
  last chunk either side holds.
- **Server:** `recorder/server.py` (Starlette on uvicorn, loopback by
  default) stores each chunk as its own file in a staging folder
  (`podcast.recorder.staging_dir`, default the system temp folder, off
  OneDrive). `recorder/store.py` makes a re-sent chunk a no-op. On *finish*
  it checks that every chunk `0..n-1` is there (it answers 409 with the
  missing numbers otherwise), joins them in order and remuxes into `.mp4`
  with `ffmpeg -c copy` (no re-encode; MP4 holds H.264 and Opus). The remux
  is needed because neither container `MediaRecorder` writes carries a
  usable duration (#336).

## Config (`config.json` → `podcast.recorder`)

| Key | Default | What |
|---|---|---|
| `host` | `127.0.0.1` | bind address |
| `port` | `8470` | port |
| `public_url` | (none) | HTTPS base for the printed links (see above) |
| `staging_dir` | system temp | where chunks wait before the join |

## Limits

- 1080p is about 5–7 GB per hour per side, and 4K about 18 GB per hour. 4K can
  outrun a home uplink, so its upload may finish after the call: the page says
  "still to send", and closing the tab does not lose what is queued.
- Safari is untested: the page records MP4 there in principle, but #336 found
  reports of long-session problems. Ask guests to use Chrome or Edge.
- Two participants per episode (one link per side).

## Verify

- `tests/test_recorder.py` covers the chunk store (idempotent re-send, the
  missing-chunk report, join order, and a real fragmented MP4 cut at arbitrary
  bytes and rejoined to its duration) and the server (token refusal,
  upload/resume/finish).
- The browser side was proven in real Chrome 154 with the real webcam (#341),
  driven by Playwright on this PC:
  - 60-minute 1080p soak with a 60 s network cut at minute 20: 3600.2 s of
    video for 3600.1 s of wall clock, 107,892 frames (29.97 fps, none
    dropped), 5.6 GB, no timestamp gap across the cut, page heap flat at
    2–3 MB.
  - 17 minutes at 4K: 5.2 GB, past the 4 GiB point where the MP4 recorder
    crashed the tab in the same test.
  - Reload mid-recording with 12 MB still queued: the reopened page closes
    the recording at its last chunk and uploads the rest, with no gap. A
    reload loses at most the last second, the chunk still in memory.
