# 2026-05-23 — Videos orchestrator: guard tag-along Threads from `post_url_th` write-back

Closes [#29](https://github.com/ferraroroberto/reporting/issues/29).

## What was done

Every successful Threads scheduling in the weekly-video orchestrator was producing a noisy warning:

```
⚠️ 20260526: scheduled OK on TH but could not write post_url_th:
"Role 'post_url_th' not present in notion_columns map. ..."
```

Per `planning/videos/README.md`, this is by design — Threads is a tag-along platform (`PLATFORMS_TAG_ALONG = frozenset({"th"})`) with no `link TH(v)` editorial column, so it has no link-based idempotency marker. The orchestrator shouldn't write a sentinel URL for it. The warning was the orchestrator trying to write the sentinel anyway, `_set_post_url` looking up a non-existent role in `editorial_columns`, and the error degrading to a logged warning. The warning was harmless functionally (caller catches it) but masked real Notion-write regressions for the *non*-tag-along platforms.

Fix: in the post-LIVE sentinel-write loop in `schedule_videos_posts.py`, `continue` when `platform in PLATFORMS_TAG_ALONG`. The guard is anchored on the existing tag-along constant (not on `editorial_columns` membership) so removing a `post_url_li / _ig / _tw` entry from a real `config.json` by mistake still surfaces the legitimate warning for that platform — AC #3 ("selective, not blanket").

## Files modified

- `planning/videos/schedule_videos_posts.py` — one-line guard before `_set_post_url` is called per platform in the LIVE write-back loop.
- `planning/videos/README.md` — extended the existing "Threads has no `link TH(v)`" gotcha to mention the orchestrator guard and what the warning now reliably indicates.

## Validation

- `& .\.venv\Scripts\python.exe -m py_compile planning\videos\schedule_videos_posts.py` — clean.
- LIVE re-verification was deliberately *not* run for this fix: a LIVE videos run today would re-attempt the originally-failing 20260526 row's TH leg and schedule a duplicate Threads video (tag-along platforms have no link sentinel by design — exactly the property the fix preserves). The change is one `if platform in PLATFORMS_TAG_ALONG: continue` inside the LIVE-only write-back loop; the guard's behavior is trivially verifiable by reading the diff. AC #3 is satisfied by the anchor choice (`PLATFORMS_TAG_ALONG` not `editorial_columns` membership) — a removed `post_url_li` entry in `config.json` still fires the legitimate warning for `li`.
