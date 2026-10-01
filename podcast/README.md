# podcast/ — episode package pipeline

Turns one raw two-track interview recording (Riverside: one video per speaker)
into the full episode package, locally. Nothing is published, posted,
scheduled or written to Notion. The only Notion access is a read of the Clips
table for past titles and LinkedIn intros (style examples).

```mermaid
flowchart LR
    T[transcribe<br/>whisper :8090] --> C[clean<br/>hub]
    T --> S[select<br/>hub]
    S --> P[copy<br/>hub]
    P --> R[render<br/>ffmpeg 1:1 + 9:16]
    C --> E[episode copy<br/>hub]
    E --> V[covers<br/>Pillow]
    R --> V
    E --> K[package<br/>.docx + website HTML]
    R --> Q[score<br/>whisper + hub]
```

## Run

```powershell
& .\.venv\Scripts\python.exe podcast_pipeline.py "<episode folder>"
& .\.venv\Scripts\python.exe podcast_pipeline.py "<episode folder>" --stages render,covers --force
```

Or from the control panel's 🎙️ podcast tab: pick the episode, run, then review
each clip (both crops, the cover, the copy and its score), the cost table and
the package files.

Each stage writes into `<episode folder>/podcast package/` and is skipped when
its output already exists, so a run that dies resumes where it stopped.
`render` and `covers` are only "done" once every clip has its files, and they
keep the clips already on disk. `--force` redoes the selected stages.

## The episode folder

The episode facts are private, so they live next to the recording in an
`episode.json`, never in this repo:

```json
{
  "guest": "Full Name", "guest_display": "Dr. Full Name", "guest_first": "Full",
  "guest_pronoun_possessive": "her",
  "tracks": {"guest": "video editing/<guest track>.mp4", "host": "video editing/<host track>.mp4"},
  "host_is_interviewee": false,
  "start_s": 196,
  "adjective": "brilliant", "date": "2024-03-28",
  "website_slug": "full-name", "youtube_url": ""
}
```

`tracks.host` is always the owner's own track, whoever asks the questions.
`host_is_interviewee: true` switches the clip and episode copy to first
person. `start_s` / `end_s` trim the pre-show chat before clip selection (the
cleaned transcript also starts there). The other keys are documented in
[`episode.py`](episode.py).

## Outputs

| Path in `podcast package/` | What |
|---|---|
| `transcript/words.json`, `turns.json`, `raw transcript.md` | word-timed transcript, both speakers |
| `<guest> - <host> - transcript.md` / `.txt` | the cleaned reading transcript |
| `clips.json`, `clips.md` | the 15 clips: times, title, Instagram caption, LinkedIn post, score |
| `clips/1x1/`, `clips/9x16/`, `clips/covers/` | the rendered clips and their cover images |
| `episode_copy.json` | YouTube, website and thumbnail copy |
| `<guest> - <host>.docx` | the episode document (YouTube, clips, thumbnails, website text, links, thank-you note) |
| `<guest> - <host> - website.html` | the "Inspiring conversations" page, paste-ready for the site's text block |
| `<guest> - <host> (1920x1080)_thumbnail.png`, `_text.png` | the episode covers |
| `metrics.json`, `metrics.md`, `scores.json` | per-stage cost table and per-clip quality scores |

Large intermediates (WAVs, frames, caption files) go to `podcast.work_dir`
(default: the system temp folder), not to OneDrive.

## Stages

- **transcribe**: each track goes to the whisper server on its own. Each
  microphone also hears the other speaker, so frames where the other track
  is much louder are muted first (the bleed gate). Whisper segments sitting on
  muted audio are dropped. A window where a 5-word phrase repeats 4+ times
  within two minutes is re-transcribed from the ungated audio: a whisper
  decoder loop repeats far more often than that, while real speech can repeat
  three times. Sub-word tokens (`" don"` + `"'t"`) are joined into words.
- **select**: the model sees numbered sentences and answers with sentence-id
  ranges, so every cut lands on a sentence boundary. Candidates that are too
  short, too long or overlapping are dropped in rank order.
- **copy**: title (lowercase, at most 5 words), LinkedIn hook + body + the
  fixed credit line and footer, and the templated Instagram caption.
- **render**: 1:1 is the dominant speaker full frame; 9:16 stacks the owner
  on top of the guest. Captions are burned in (ASS): Sora ExtraBold, white
  with a black outline, one keyword per chunk in `#FDEC01`. 24 fps, H.264,
  loudness-normalised.
- **score**: per clip, 1 to 5 on the rubric: hook in the first 3 s and
  self-contained idea (hub, text), caption accuracy (word error rate against
  a fresh whisper pass of the rendered audio), and framing of both crops (hub,
  one frame each).

## Config (`config.json` → `podcast`)

`episodes_root`, `whisper_url`, `llm_hub_base_url`, `models` (hub alias per
role), `llm_rates_usd_per_mtok` (list prices for the cost table: the hub runs
on the subscription, so the cost is a metered-API equivalent),
`clips_per_episode`, `clip_min_s` / `clip_max_s`, `video_encoder`, `fonts`,
`host` (name, headshot, brand mark), `linkedin_footer`, and
`notion.clips_db_id` for the style examples. See `config/config_example.json`.
