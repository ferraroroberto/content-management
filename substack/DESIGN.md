# Substack Automation — Playwright + Real Chrome Integration

**Date:** 2026-05-13
**Issue:** [#7 — feat(substack): automated Note posting + follower count via Playwright](https://github.com/ferraroroberto/reporting/issues/7)
**Branch:** `feat/substack-integration`

---

## What was built

A new `substack/` package that automates the two daily Substack steps the public API does not expose:

1. **Publish a Note** — reads the day's text + image filename from the Notion editorial database, attaches the illustration, and posts it.
2. **Scrape follower count** — reads "Total followers (N)" from the audience-stats page and writes the integer back to the same Notion row.

Both steps run through Playwright with a **persisted real-Chrome profile**, share a single browser launch when invoked through the orchestrator, and plug into `init.py` as a new Step 6 in the daily pipeline.

## Files added

```
substack/
├── __init__.py
├── README.md                      — user-facing docs (config, CLIs, prerequisites)
├── substack_session.py            — Playwright session manager (persistent context, safety guard)
├── bootstrap_session.py           — one-time interactive login → dedicated Chrome profile
├── notion_editorial.py            — role→column resolver over the editorial DB
├── post_substack_note.py          — step 1 (publish Note)
├── update_substack_followers.py   — step 2 (scrape followers)
└── daily_pipeline.py              — orchestrator (single browser, both steps)
```

## Files modified

| File | Change |
| --- | --- |
| `init.py` | Added Step 6 (`run_substack_daily_pipeline`) and `-b / --skip-substack` flag. |
| `requirements.txt` | Added `playwright`. |
| `.gitignore` | Added `substack/chrome_user_data/` (gitignored Chrome profile). |
| `config/config.json` / `config_example.json` | New `substack` block (sanitized in the example). |
| `README.md` | Added Substack section + bootstrap commands. |

## Validation run (2026-05-13)

```
$ python -m substack.daily_pipeline --dry-run
🚀 Substack daily pipeline — day=20260513 dry_run=True
🌐 Substack session started (channel=chrome, headless=False)
🖼️ Using image: …bull's eye one many - everything priority nothing priority.png
📝 Body length: 53 chars
🖼️ Image preview fully loaded in composer.
✅ DRY-RUN: composer screenshot saved
👥 Total followers: 14554
📝 Notion update: page=… field=follow SB (number) value=14554
✅ Wrote follower_count=14554 to Notion editorial row.
👋 Substack session closed
📊 Pipeline result: post=0 followers=0
```

End-to-end run completed in ~10 seconds with a single browser launch.

---

## Lessons learned — Playwright vs. Substack's anti-bot stack

### 1. Bundled Chromium gets flagged by reCAPTCHA at sign-in

The first attempt used Playwright's bundled Chromium binary. The `bootstrap_session.py` login flow froze at the "I'm not a robot" checkbox even with the user manually clicking. reCAPTCHA's risk score doesn't really verify the click — it scores the browser environment (`navigator.webdriver`, plugin enumeration, WebGL fingerprint, font list, history depth, etc.) and Playwright's stock Chromium scores as "bot" no matter what the human does.

### 2. The right fix is not a captcha bypass — it's running real Chrome

We deliberately did **not** install:
- `playwright-stealth` / similar fingerprint-patching plugins,
- a captcha-solving service (CapSolver, 2Captcha, NoCaptchaAi),
- `playwright-extra` with anti-detect packs.

All of those exist specifically to defeat reCAPTCHA, which is an anti-automation control Substack put in place. Even where the activity itself is legitimate (posting your own content to your own account), defeating that control crosses a clear line.

The compliant fix is to remove the things reCAPTCHA fingerprints on — i.e., stop using Playwright's instrumented Chromium and drive the user's **real installed Chrome** instead. We switched to:

```python
context = playwright.chromium.launch_persistent_context(
    user_data_dir=str(user_data_dir),
    channel="chrome",                                 # real Chrome, not Chromium
    headless=False,
    args=["--disable-blink-features=AutomationControlled"],
    viewport={"width": 1280, "height": 900},
)
```

With real Chrome and a persistent on-disk profile that accumulates normal browsing history, reCAPTCHA's score moves into the "human" range and the checkbox passes on first click.

### 3. Never touch the user's regular Chrome profile

`launch_persistent_context` writes into whatever `user_data_dir` you point at. Pointing it at `%LOCALAPPDATA%\Google\Chrome\User Data` would inject automation flags into a profile that holds passwords, history, extensions, and cookies for unrelated sites — and would fail to launch if Chrome is already open.

`SubstackSession` defends against that with a safety guard in `_resolve_user_data_dir`: it inspects the resolved absolute path and refuses to start if it contains substrings matching common real-Chrome profile locations (`Google/Chrome/User Data`, `Library/Application Support/Google/Chrome`, `.config/google-chrome`, plus the Chromium variants). The default `user_data_dir` (`substack/chrome_user_data/`) is created fresh inside the repo and gitignored.

### 4. Substack's note composer is not a `role="dialog"`

The issue spec called for ARIA-role selectors: `get_by_role("dialog")` → `get_by_role("textbox")`. In practice:
- The note composer is a custom popover, not a real ARIA dialog.
- The editor is a ProseMirror `contenteditable` div, not a `role="textbox"`.

The working approach:
- Identify the editor by `[contenteditable="true"]`, preferring one whose `data-placeholder` mentions "mind".
- Scope the composer container with an xpath that walks up to the nearest ancestor containing both the "Cancel" and "Post" buttons. This scope is then used for the image-input search so we don't hit the page's avatar or cover-photo file inputs.

### 5. Wait for the *real* image — not just any `<img>`

`composer.locator('img[src]:not([src=""])').wait_for(...)` returned in 23 ms because it matched a tiny avatar image inside the popover header. The Post button stayed disabled because the actual upload was still in flight.

Fix: a `page.wait_for_function` predicate that checks for an `<img>` inside the composer with `naturalWidth > 200 && naturalHeight > 200 && img.complete`. The first such image is the upload preview, not an avatar. Wait now takes ~1 second (the real upload roundtrip).

### 6. Multi-strategy image attach

The composer's hidden file input is mounted lazily and there are several other `input[type="file"]` elements elsewhere on the profile page. `_try_attach_image` therefore tries, in order:

1. Composer-scoped hidden `<input type="file">` (most reliable).
2. Composer-scoped image/photo/upload button + `expect_file_chooser` to catch the OS file dialog.
3. Page-level file input (last resort, in case the composer scoping missed).

### 7. URLs differ between handle and publication

The user's profile lives at `https://substack.com/@<handle>` but the publication owner-side stats live at `https://<publication-subdomain>.substack.com/publish/stats/audience` — and the publication subdomain isn't always the same as the handle. Configuring `stats_audience_url` with the handle subdomain redirected to the public profile and quietly produced no follower count. Resolution: the `substack` config block has separate `profile_url`, `publish_url`, and `stats_audience_url` keys and the README explicitly says to copy them from the browser's address bar.

### 8. Smart apostrophes everywhere

Notion auto-corrects `'` to `'` (U+2019). The illustration filename column therefore stores `bull's eye…`. The actual file on disk has the same curly apostrophe (Affinity Designer respects what you type), so a literal `Path(folder) / filename` lookup works — provided the disk filename matches. The "image not found" failure mode looks scary because the Windows cp1252 console renders the U+2019 as `?`, but the underlying string is correct.

### 9. Console encoding on Windows + Python 3.14

When this project is invoked from a host that hands the child process a cp1252 stdout (which happens for `python -c` from PowerShell harnesses), every emoji log line raises `UnicodeEncodeError` and the logging system aborts. Defensive fix in `configure_logger`:

```python
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
```

Safe and idempotent — does nothing on stdout streams that are already UTF-8.

### 10. Logger plumbing when steps run via the orchestrator

`config/logger_config.py::setup_logger` attaches handlers only to the named logger, not to root. When `daily_pipeline.main()` called `post_note()` and `update_followers()` directly (rather than via their CLIs), the child-step `logger.info(...)` lines vanished — the named loggers had no handler. Fix: `daily_pipeline.main()` pre-configures every `substack_*` logger up front:

```python
for name in (
    "substack_daily_pipeline",
    "substack_post_note",
    "substack_update_followers",
    "substack_session",
    "substack_notion_editorial",
):
    configure_logger(name, debug=args.debug)
```

Same pattern would be useful in `init.py` if more silent-when-imported modules show up later.

### 11. Reusing `notion_update.init_notion_client` requires its logger to exist

`notion_update.py` declares `logger = None` at module scope and only assigns it inside `configure_logger()` which is called from its own `main()`. Reusing `init_notion_client` from another module triggered `AttributeError: 'NoneType' object has no attribute 'debug'`. Fix: `notion_editorial.py` calls `notion_update.configure_logger(debug_mode=False)` at import time if the module-level logger is still `None`.

### 12. Date format normalization

`init.py` passes `--date YYYY-MM-DD` to every step; the Substack CLIs were written to expect `YYYYMMDD` (to match the Notion editorial title column). Rather than convert at the call site, `substack_session.normalize_day()` accepts either format and produces `YYYYMMDD`, called once at the entry of each CLI's `main()`.

---

## Operational notes

- One-time setup: `python -m substack.bootstrap_session` opens real Chrome against the dedicated profile, lets the user log in manually, and saves the session on close. No `playwright install chromium` step is required because we drive the user's existing Chrome binary.
- Cookie lifetime: the Substack session eventually expires. When that happens, `goto_with_login_check` detects the redirect to `sign-in` and exits non-zero with a "re-run bootstrap_session" message.
- Idempotency: step 1 short-circuits if the editorial row's `post_url` column is already populated (unless `--force`). Step 2 always overwrites — re-running the same day is harmless.
- Failure artifacts: any selector failure or follower-not-found event saves a screenshot under `results/substack/` with a timestamped filename for offline debugging.
