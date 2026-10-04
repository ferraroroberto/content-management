# recorder/ — self-hosted two-side podcast recorder

Each participant opens their own link. The page holds a **peer-to-peer video
call** between the two, and records each side's camera and mic **locally at
full resolution**, uploading in chunks while it runs. The finished files land
in the episode's `video editing/` folder, which is where `podcast_pipeline.py`
reads its tracks from. This replaces the one job Riverside did for the
podcast. The design and the probes behind it are in #336. Build steps: the
recorder (#341), the call (#342), and the track sync (#343, the pipeline's
`sync` stage, see `podcast/README.md`).

## Run

```powershell
& .\.venv\Scripts\python.exe recorder_server.py "<episode folder>"
& .\.venv\Scripts\python.exe recorder_server.py "<folder name under podcast.episodes_root>" --port 8470
```

It prints two links, one for the **host** side and one for the **guest**
side, and serves until stopped (Ctrl+C). The links are random tokens kept in
`<episode folder>/recorder.json`, so a restart keeps the links already sent.
A request with an unknown token gets a 404.

Each side opens its link in **Chrome or Edge**. The call starts as soon as
both pages are open. Each side picks camera and microphone, chooses 1080p
(default) or 4K, and presses *Start recording*; *Stop* ends it. The page shows
how much is uploaded. The files, per side:

- `video editing/recorder - <side> - <date time>.mp4`: this side's camera
  and mic, full quality.
- `video editing/recorder - <side> remote-ref - <date time>.mp4`: the
  **other** side's voice as heard over the call (Opus, 32 kbps), recorded
  while this side records. It is the reference the sync stage (#343)
  aligns the two full-quality tracks against. A call that drops and comes
  back while recording gives a second reference file.
- Beside each file, a `.json` sidecar: its recording id, and for a
  reference, which main recording of that side it ran beside and where in
  it the reference starts (`offset_in_main_s`, measured on the page's own
  clock). The sync stage pairs files by these, never by name.

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

**The call needs a TURN relay for strict networks.** STUN (a public server)
gets most home connections through, but some networks (mobile carriers,
corporate firewalls) allow no direct peer-to-peer path, and then the media
must go through a relay. Funnel carries only HTTPS, so it cannot be that
relay. Use a hosted TURN service: put its URLs in `podcast.recorder.turn.urls`
(e.g. `turn:<host>:443?transport=tcp`) and its credential in `.env` as
`RECORDER_TURN_USERNAME` / `RECORDER_TURN_CREDENTIAL`. Without TURN the
server says so at start, and some guests may not connect.

## How it works

- **Call:** the page signals over a WebSocket on the same server (`/ws/<token>`),
  which only relays offers, answers and ICE candidates between the two sides
  (one socket per side; a reconnecting page replaces its stale socket). The
  media is peer to peer. The host always opens the call; the guest answers
  with its own tracks on the transceivers the host's offer made, so a join
  never has two offers crossing. Later renegotiation uses "perfect
  negotiation" (the guest yields on a collision). Each page load names itself,
  so a reloaded peer restarts the call while a signalling blip leaves a
  working call alone. The
  same camera stream feeds the recorder at full resolution and the call
  sender, scaled down to 720p at 1.5 Mbps.
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
| `stun_urls` | Google's public STUN | STUN servers for the call |
| `turn.urls` / `.username` / `.credential` | (none) | hosted TURN relay; the credential belongs in `.env` (`RECORDER_TURN_USERNAME`, `RECORDER_TURN_CREDENTIAL`), which wins over the config |

## Limits

- 1080p is about 5–7 GB per hour per side, and 4K about 18 GB per hour. 4K can
  outrun a home uplink, so its upload may finish after the call: the page says
  "still to send", and closing the tab does not lose what is queued.
- Safari is untested: the page records MP4 there in principle, but #336 found
  reports of long-session problems. Ask guests to use Chrome or Edge.
- Two participants per episode (one link per side); no screen sharing or chat.
- The hosted TURN relay and a call across real networks (one side on mobile
  data) are still to be proven: #342 stays open for them.

## Verify

- `tests/test_recorder.py` covers the chunk store (idempotent re-send, the
  missing-chunk report, join order, and real MP4 and WebM streams cut at
  arbitrary bytes and rejoined to their duration), the server (token refusal,
  upload/resume/finish, the reference file's name) and the signalling (bad
  link or peer id refused, presence, relay, one socket per side, ICE config).
- `tests/test_recorder_call_browser.py` runs the call end to end against a
  local server: two pages in Playwright's Chromium with a fake camera and mic
  connect, the call sender carries the 720p / 1.5 Mbps cap while each side
  records 1080p, and both sides upload a main recording plus a reference that
  points at it. It skips without ffmpeg or Playwright's Chromium
  (`playwright install chromium`). This test found the guest's own offer
  colliding with the host's on join, which stuck about one call in three
  before ICE. Since then the host alone opens the call, and the guest answers
  with its tracks.
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
  - Call between two Chrome instances on this PC (real webcam, and Chrome's
    fake camera): connected in 3 s, call picture 720p while both recordings
    stay 1920×1080, a reference file per side carrying the other side's
    audio, and the call back 1.5 s after one side reloaded.
