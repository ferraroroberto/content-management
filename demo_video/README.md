# demo_video/ — product demo videos from a storyboard

Turns screen recordings of an app plus music into a finished, animated demo video (16:9, 1:1 or 4:5), from one `demo.json` storyboard. The renderer is a [Remotion](https://www.remotion.dev/) template driven entirely by data: a new video is a new `demo.json`, not new code. Nothing is published, posted or uploaded.

This is Step 1/4 of the demo-video pipeline (#359). Media prep and hard-stop checks (#360), the recorder (#361) and the `/demo-video` skill with its runbook and control-panel tab (#362) build on it.

```mermaid
flowchart LR
    J[demo.json<br/>cuts · copy · scenes · soundtrack] --> V{validate<br/>copy keys · clips · beats}
    M[marks.json<br/>beat start/end per video] --> V
    V --> R[resolve_cut<br/>props per cut]
    R --> X[Remotion template<br/>demo_video/remotion]
    D[media/<br/>clips + music] --> X
    X --> O[out/&lt;cut&gt;.mp4<br/>or .preview.mp4]
```

## Run

```powershell
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --status
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --cut en-linkedin --preview
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>"              # every cut, final 1080p
& .\.venv\Scripts\python.exe demo_video_pipeline.py "<demo folder>" --force      # redo existing outputs
```

| Flag | What |
|---|---|
| `--status` | Validates the storyboard, lists missing media, says which cuts are rendered. Writes nothing; exits 2 on an invalid storyboard or missing media. |
| `--cut <id>` | Only that cut. |
| `--preview` | Half scale, to `out/<cut>.preview.mp4`. About 1¾ min for 83 s. Review with this, not with single stills (Remotion bundles per still, about 40 s each). |
| `--force` | Re-render a cut whose output exists. |

A final 1080p render takes about 2½–3 min per 83 s cut on this PC.

**One-time setup:** `npm install` in `demo_video/remotion/` (Node.js 20+). `node_modules` is gitignored and lives there, never under OneDrive. The optional `demo_video.node_root` in `config/config.json` points at another install.

## A demo folder

```
<demo folder>/              (outside the repo — OneDrive is fine for this part)
  demo.json                 the storyboard
  marks.json                {video: {beat: [start_s, end_s]}} — written by the recorder (#361)
  media/                    clips (CFR H.264 .mp4) and music; the `media_dir` field renames it
  out/                      renders, plus out/.props/<cut>.json (the resolved props)
```

The worked example is [`examples/facilitation-suite/`](examples/facilitation-suite/): a synthetic English demo of `facilitation-suite`, 10 scenes, 83 s, three cuts (16:9, 1:1, 4:5). Its media is not in the repo; copy `demo.json` + `marks.json` into a folder next to a `media/` with `en/{stage,presenter,plan,results}.mp4` and `music/en.mp3`.

## demo.json

| Field | What |
|---|---|
| `title`, `fps` | Name and frame rate (30). |
| `product` | `name`, `icon_paths` (24×24 SVG path data, Lucide-style strokes) and two gradient `colors` for the logo tile. |
| `media_dir`, `marks` | The media folder and marks file, relative to the demo folder; `{lang}` is the cut's language. |
| `clips` | Clip id → `{file, marks?}`: the file (relative to `media_dir`, `{lang}` allowed) and its key in the marks file (default: the clip id). |
| `copy` | Language → key → string or list. Every `"@key"` in a scene is replaced by `copy[<cut language>][key]`. |
| `cuts` | `{id, lang, aspect: 16:9 \| 1:1 \| 4:5, public, soundtrack}`. One render per cut. |
| `scenes` | In order; each `{id, type, seconds, fade_s?}` plus its type's fields (below). |

**Clip ref:** `{"video": "stage", "beat": "map", "offset": 2.0, "rate": 1.45, "crop": {x, y, w, h}?}` starts `offset` seconds after the beat's start in that video (or after the video's start without `beat`) and plays at `rate`. `crop` is in source pixels of the 1920×1080 recording.

**Soundtrack track:**

```json
{"file": "music/a.mp3", "licence": "free | private-only",
 "start": 0 | {"scene": "groups", "offset_s": -1.5},
 "end": "end" | {"scene": "groups", "offset_s": 1.5},
 "source_start_s": 12.0 | "align_end_tail_s": 1.0,
 "volume": 0.9, "fade_in_s": 0.5, "fade_out_s": 2.5}
```

- `align_end_tail_s` trims the track's start so it ends that many seconds before its own end, exactly when its window ends. This is how a second track takes the climax to the video's end.
- `licence` is read by the licence check (#360): a `public` cut must not use a `private-only` track.

### Scene catalogue

Every scene keeps the reference layout in 16:9. In 1:1 and 4:5 the caption goes on top and the screens go below it:
- in 1:1, two screens sit side by side;
- in 4:5 they stack, with the second one as a picture-in-picture;
- a full-bleed clip becomes a full-width 16:9 band, so nothing in the recording is cropped away.

| `type` | Shows | Fields |
|---|---|---|
| `full_bleed` | A clip filling the frame with a slow zoom, a big caption and chat bubbles popping in | `clip`, `caption`, `zoom`, `focus`, `chat {messages, senders?, sender_step, every_s, start_s, max}` |
| `tiles_statement` | A grid of camera-on meeting tiles with chat bubbles; the caption swaps from `before` to `after` | `tiles`, `chats`, `talkers` (tile index per chat), `before`, `after`, `swap_s` |
| `title_card` | A centred caption | `caption` |
| `brand` | Logo, product name, subtitle | `sub` |
| `steps_device` | A numbered step list next to a device playing a clip; the active step follows the clip | `steps` (`[{title, sub}]`), `step_at` (clip refs where steps 2..n start), `device`, `clip` |
| `split_window_monitor` | What participants see (a meeting window) next to the presenter's monitor | `caption`, `window {label, clip}`, `monitor {label, clip}` |
| `crop_zoom` | A cropped close-up panel of one screen next to a meeting window | `caption`, `panel` (clip ref with `crop`), `window` |
| `side_by_side` | Two or more meeting windows, staggered in | `caption`, `windows`, `stagger_s` |
| `device_caption` | A caption next to a device playing a clip | `caption`, `device` (`laptop` \| `monitor` \| `window`), `clip` |
| `window_states` | A meeting window playing clip segments, with a state legend lit from measured times (e.g. a timer's colours) and a "⏩ ×rate" chip on a sped-up segment | `caption`, `segments [{clip, seconds?}]`, `legend {labels, colors, segment, anchor, changes [[t, state]]}`, `speed_chip` |
| `outro` | A blurred clip behind the logo, tagline, product name and footer; fades to black | `clip`, `tagline`, `footer` |

A `caption` is `{kicker?, title, sub?, color?, size?, width?}`. Colours are a palette name (`red`, `green`, `yellow`, `blue`, `purple`, `slate`) or any CSS colour; `size` and `width` are 1920×1080 design px. `\n` breaks a line.

## Gotchas

- **Clips must be constant-frame-rate H.264.** Playwright's `.webm` (VP8, variable rate) stutters and seeks badly in `OffthreadVideo`; transcode first (#360 automates it): `ffmpeg -i rec.webm -vf "fps=30,format=yuv420p" -c:v libx264 -crf 14 -g 15 -an clip.mp4`.
- **Remotion copies `media_dir` into its bundle on every render.** Keep only what the demo uses there.
- **Fonts** (Inter, Patrick Hand) load from Google Fonts at render time, so the render needs the network.
- **Remotion's licence** is free for individuals and companies of up to 3 people. `remotion`, `@remotion/cli` and `@remotion/google-fonts` are pinned to the same version.
- **This repo is public.** A private demo's `demo.json`, recordings and music stay in its own folder; the committed example is synthetic.

## Files

| File | What |
|---|---|
| `storyboard.py` | `demo.json` schema (Pydantic), `validate` (copy keys, clips, beats, anchors), `resolve_cut` (props), `missing_media` |
| `render.py` | Writes `out/.props/<cut>.json` and runs the Remotion CLI with node (no `npx.cmd` shell) |
| `remotion/src/ui.tsx` | Components: `Clip`, `ZoomWindow`, `Monitor`, `Laptop`, `Caption`, `ChatStream`, `Label`, `Logo`, `SceneFade`, `Backdrop`, `Pop` |
| `remotion/src/scenes.tsx` | One component per scene type, landscape and stacked layouts |
| `remotion/src/Demo.tsx`, `Root.tsx` | The scene sequence and soundtrack; one `Demo` composition sized from the props |
| `examples/facilitation-suite/` | The worked example (`demo.json` + `marks.json`) |
| `../demo_video_pipeline.py` | The CLI |
