# Substack native HTTP API integration

How this repo talks to Substack over its private HTTP API (cookie-auth) instead
of driving a browser, and why. Companion to `planning/substack/README.md` (the
Playwright integration, which is **kept** as an alternative path — this does not
replace it). Originated from the spike in issue #91.

## Why a native path

The Playwright integration works but is fragile: DOM selectors, a persistent
real-Chrome profile (single-instance lock, serialized access), reCAPTCHA at
sign-in, and a headed browser launch per run. Substack's *official* Developer API
is a single profile-search endpoint — it cannot read posts, write posts, or read
the follower count, so it covers nothing we need.

Everything we actually want is reachable through the private endpoints the
Substack web app itself calls, authenticated with **our own session cookie**.
This is authenticated HTTP to our own account — no captcha bypass and no
fingerprint spoofing (a different, lighter category than the anti-bot concerns in
the Playwright `README.md`). Functionally it is *more* robust than the Playwright path: no DOM
selectors, no reCAPTCHA, no shared-profile lock, no headed launch. The trade-off
is that these endpoints are **undocumented and can change without notice**.

## Authentication: cookie harvest, not password

We never store a Substack password. The session cookie is harvested from the
dedicated Chrome profile that `bootstrap_session` already logs in:

1. `extract_session.py` launches that profile via `SubstackSession` (real Chrome),
   confirms the login is still valid, then reads `context.cookies()` and the
   browser User-Agent, and writes `planning/substack/api_session.json` (gitignored).
2. `api_client.py::load_session` builds a `requests.Session` from those cookies +
   UA for every subsequent call — **no browser launch** until the cookie expires.

### Cookies that matter

| Cookie | Role | Observed lifetime |
| --- | --- | --- |
| `substack.sid` | The session auth cookie (httpOnly). | ~89 days |
| `substack.lli` | Login companion. | ~89 days |
| `cf_clearance` | Cloudflare bot-clearance token. | ~1 year |
| `__cf_bm` | Cloudflare bot-management, short-lived. | ~30 min |

Two practical consequences:

- **The User-Agent must match.** `cf_clearance` is bound to the UA that solved
  Cloudflare's challenge, so the HTTP session presents the same UA captured at
  harvest time. A mismatched UA risks a Cloudflare block.
- **Re-harvest cadence is ~quarterly.** `substack.sid` lives ~89 days — much
  longer than the Playwright README implied. On 401/403 the client raises
  `SessionExpiredError`, the signal to re-run `extract_session` (same cadence as
  re-running `bootstrap_session`).

## Endpoint map (what the spike proved)

All under `https://substack.com/api/v1` unless noted. Write/pull beyond the
follower count goes through the `python-substack` library (it owns publication
resolution and the ProseMirror body builder); the follower count is a direct GET
to avoid the library's construction-time round-trips.

| Capability | Route | Notes |
| --- | --- | --- |
| Follower count | `GET /user/profile/self` → `followerCount` | Same integer the Playwright "Total followers (N)" scrape reads. Daily path. |
| List published posts | `GET /<pub>/post_management/published` | Returns an envelope `{posts, total, …}`; posts carry `title`/`slug`/`post_date`/`type`/`audience` but **no** body or `canonical_url`. |
| Full post body | `GET /<pub>/posts/by-id/{id}` | Adds `body_html` + `canonical_url`. One extra GET per post (`--with-body`). |
| Create draft | `POST /<pub>/drafts` | Private; emails no one. |
| Read draft back | `GET /<pub>/drafts/{id}` | Used to verify the stored body round-trips (see *Body shape* below). |
| Edit draft | `PUT /<pub>/drafts/{id}` | |
| Pre-publish validate | `GET /<pub>/drafts/{id}/prepublish` | Returns `{errors, suggestions}`; does not publish. |
| Publish | `POST /<pub>/drafts/{id}/publish` | **Irreversible** — emails the whole list. Gated behind explicit `--confirm`; never in the cron. |
| Own Notes + engagement | `GET /reader/feed/profile/{user_id}?types[]=note` | Paginated by `nextCursor` (12 items/page). Each item's `comment` **is** the Note. Daily path — see *Notes* below. |
| Upload an image | `POST /image` | Body `{"image": "data:<mime>;base64,…"}` → `{url}`. Shared with the post editor. |
| Make a Note attachment | `POST /comment/attachment` | Body `{"url": <cdn url>, "type": "image"}` → `{id}`. |
| Publish a Note | `POST /comment/feed` | Body `{"bodyJson": …, "attachmentIds": […], "replyMinimumRole": "everyone"}`. **Immediately public** — Notes have no draft state. |
| Delete a Note | `DELETE /comment/{id}` | Returns `{}`. |
| React to ("like") a Note | `POST /comment/{id}/reaction` | Body `{"reaction": "❤"}` — the only reaction type the composer sends. Idempotent (a second POST is a no-op). |
| Remove a reaction | `DELETE /comment/{id}/reaction` | Same body. Idempotent (a DELETE with no existing reaction is a no-op). |

## Notes: a Note is a `comment`, and engagement comes free

A Substack Note is not its own entity type — it is a `comment` with
`type == "feed"` and a `null` `post_id`, surfaced through the reader feed. Asking
the profile feed for `types[]=note` returns our own Notes newest-first, and each
payload already carries the engagement numbers, so there is **no** per-note
request: what the Playwright path did with a feed scroll plus one page load per
note is `1 + ceil(n/12)` GETs.

The field mapping (issue #185), against `reporting/scrape_client/substack.py`'s
scrape:

| Record field | Native source | Was (Playwright) |
| --- | --- | --- |
| `post_id` | `https://substack.com/@<handle>/note/c-{comment.id}` | same URL, read off the feed anchor |
| `posted_at` | `comment.date`, **converted UTC → local** | the timestamp anchor's `title`, rendered in browser-local time |
| `num_likes` | `comment.reaction_count` | `button[aria-label='Like']` text |
| `num_comments` | `comment.children_count` | `button[aria-label='Comment']` text |
| `num_reshares` | `comment.restacks` | `button[aria-label='Restack']` text |
| `is_video` | an attachment with `type == "video"` | a `<video>` in the note container |
| `is_teaser` | a `post` attachment, or a `link` attachment whose `linkMetadata.url` contains `/p/` | an `<a href*='/p/'>` card in the container |

Three things worth keeping in mind:

- **The engagement triple is the same source the UI renders.** Substack's own
  feed bundle draws the note toolbar from `reaction_count` / `children_count` /
  `restacks` — we are reading the numbers the DOM scrape was reading *off*, not a
  parallel statistic that could diverge.
- **`posted_at` must be converted to local time before the date is taken.** The
  API returns UTC; the Playwright path read a browser-local rendering, and the
  posts consolidator matches on local days (`posted_at = date - 1 day`). Slicing
  the first ten characters of the UTC string would silently shift late-evening
  notes onto the wrong day. `note_posted_date` does the conversion and
  `tests/test_substack_native_notes.py` pins it.
- **Teaser detection keys on the `/p/` URL, not merely on "has a link".**
  Ordinary daily notes *do* carry `link` attachments (an outbound article link),
  so testing for an attachment type alone would drop real notes. Caveat: no
  teaser note existed in the 144 most recent, so this branch is implemented from
  the shape of the `link`/`post` attachments rather than verified against a live
  specimen — the one part of the mapping without direct evidence.

### Publishing a Note natively

`substack.note_source = "native"` swaps `post_substack_note.py`'s publish step
from the Chrome composer to three HTTP calls (`/image` →
`/comment/attachment` → `/comment/feed`); everything else in that script — the
Notion read, the idempotence guard, the empty-body refusal, the `post_url`
write-back — is shared. Rollback is the same one key back to `"playwright"`.

Two things this fixes beyond removing the browser:

- **The permalink is exact.** The Playwright path published, then re-loaded the
  profile and took the topmost `/note/` anchor — a race against anything else
  posting. The native path uses the id the create call returns.
- **`bodyJson` is built literally.** The body is a Notion column that can contain
  `*`, `[` or backticks; it is inserted as plain text nodes, never markdown-parsed.
  Blank lines become separate `paragraph` nodes, matching how the composer
  stores multi-paragraph notes.

Caveats worth knowing:

- **The create response does not echo `handle`** (verified live), so the
  permalink is built from `config.substack.handle`, falling back to
  `fetch_self_handle()` — the same source the reporting path uses, so a publish
  and a later scrape cannot key on different URLs.
- **Substack rewrites the body server-side.** A posted note whose text contains
  something URL-shaped comes back with the doc split into extra text nodes
  carrying `link` marks. The rendered text is unchanged and the browser composer
  behaves identically — so this is parity, not a defect — but do not expect the
  stored `body_json` to be byte-identical to what was sent.
- **There is no dry-run at the API layer**, because a Note has no draft state.
  `--dry-run` is enforced in `post_substack_note.py` *before* the publish call.
- **Video notes are not supported natively.** They upload through a separate mux
  pipeline (`mux_asset_id` / `mux_playback_id` on the attachment) that was not
  reverse-engineered; `post_substack_video_note.py` stays on Playwright.

### Reacting to ("liking") a Note (issue #186)

`react_to_note(note_id)` / `unreact_to_note(note_id)` in `api_client.py` wrap
`POST` / `DELETE /comment/{id}/reaction`. There is no existing "like" workflow
in this repo to slot into, so these are exposed as a small manual CLI,
`api_like.py`, alongside `api_pull.py`/`api_create.py` — not wired into any
pipeline.

The write route was **not** discoverable from the JS bundles either (same
lesson as the Note-publish routes) — found by driving the real composer with
a `page.on("request")` listener attached while clicking the heart icon on a
throwaway note. A note permalink page renders a *feed* (the subject note plus
recommended notes), each with its own Like button, so the click was scoped to
the subject note's own container the same way
`reporting/scrape_client/substack.py::_scrape_note_permalink` already scopes
its engagement read — via the timestamp anchor pointing at `/note/c-<id>`,
walked up to the smallest ancestor owning a `button[aria-label='Like']`.

Verified live, end-to-end, three separate ways: (1) the browser-driven
capture itself (click → `POST .../reaction` → `reaction_count` 0→1; click
again → `DELETE .../reaction` → 0), (2) a pure-HTTP round trip with no
browser at all, confirming the minimal body `{"reaction": "❤"}` is sufficient
(no `tabId`/`publication_id` needed, despite the browser sending them), and
(3) double-POST / double-DELETE idempotency (no error, count unchanged). Every
probe created one throwaway note, reacted/un-reacted, then deleted it —
nothing was left on the account.

### Verified by A/B against the browser path

Both `fetch_posts` implementations were run back-to-back against the live
account: **10/10 records identical on every field**, no set difference either
way, in **0.9 s (native) vs 37.0 s (Playwright)**.

## How it wires into the reporting pipeline

`reporting/social_client/social_api_client.py::get_api_data` dispatches on a
`source` field per config block:

- `"playwright"` → `reporting/scrape_client/<platform>.py`
- `"native"` → `reporting/scrape_client/<platform>_native.py` (new)
- otherwise → RapidAPI

`substack_profile.source = "native"` routes the daily follower count to
`substack_native.fetch_profile`, which returns the identical `{"num_followers": N}`
envelope, so `save_results` → `data_processor` → `profile_aggregator` →
`notion_update` are all unchanged. Flip the flag back to `"playwright"` to fall
back to the browser scrape. The block keeps its `api_url`/`api_key` keys because
the endpoint loop only iterates blocks that have an `api_url`.

## Body shape: ProseMirror, built explicitly

A draft body is ProseMirror JSON, not HTML or markdown. The weekly newsletter's
sectioned layout (a heading per topic, a bullet list of linked article titles) is
built node-by-node in `planning/substack/api_client.py::build_section_nodes`:

```
paragraph    — optional intro (the must-read line)
heading      — attrs.level = 2, one per topic
bullet_list  — list_item > paragraph > text node carrying a `link` mark
```

Two findings from building it, both verified against the live API:

- **`Post.from_markdown` cannot express this shape.** Given a `##` heading
  followed by `- [title](url)` lines, it folds the bullet lines into the
  *heading's own text node* — the list and every link are destroyed, silently.
  The nodes are therefore constructed directly. `tests/test_substack_draft_sections.py`
  pins the resulting structure so a refactor back to `from_markdown` fails loudly.
- **Node names are snake_case** (`bullet_list`, `list_item`), not the camelCase
  many ProseMirror schemas use. Confirmed by creating a draft and reading it back
  via `GET /drafts/{id}`: the stored body matched what was sent, link marks
  intact.

Article titles are emitted as **literal text nodes**, never run through
`parse_inline`. Titles are scraped from arbitrary sites and routinely contain
`*`, `[` or backticks that markdown parsing would mangle.

## Known fragility (be honest)

- Endpoints are undocumented and can change without notice. Concrete example: the
  `python-substack` helper `get_publication_subscriber_count` is already stale —
  it reads a `subscriberCount` key the endpoint no longer returns (it now returns
  a `subscribers` *list*). We don't rely on that helper.
- The library's `get_published_posts` returns the raw envelope; callers must
  unwrap `["posts"]` (`SubstackAPI.list_published` does this).
- The Note write routes are **not discoverable by reading the JS bundles** — the
  composer is a lazily-imported webpack chunk, so nothing served on `/home` or
  `/notes` contains them. They were captured by driving the real composer once
  with a network listener attached. A future breakage is diagnosed the same way,
  not by grepping bundles.
- Beware: a `GET` on a POST-only route returns **404, not 405** (Express routes
  per method), so "GET says 404" is *not* evidence that a write route is absent.
  An early pass in the spike drew the wrong conclusion from exactly this.
- Video Notes remain Playwright-only (separate mux upload pipeline).
- No rate limiting was observed across the spike's calls (including a 12-page
  cursor walk over 144 notes), but it is unmeasured at daily-cron scale.

## Scope today vs. follow-ups

Shipped: native follower count (daily default) + manual archive pull + manual
draft create/edit/prepublish/publish (issue #91), and the weekly newsletter's
`substack-draft` step (issue #184) — `newsletter/substack_draft.py` builds a
private draft edition from the Notion articles DB, never publishing.

Note that the *editorial* database was the wrong source for an edition: it has no
newsletter/edition columns (its Substack columns drive the daily short-form
Note). The newsletter's own articles + newsletter DBs are the real source, which
is why the step lives in `newsletter/` rather than `planning/substack/`.

Also shipped (#185): native **note engagement**
(`substack_posts.source = "native"` → `substack_native.fetch_posts`) and native
**Note posting** (`substack.note_source = "native"` → `post_substack_note.py`).
Both keep Playwright as a one-key rollback.

Also shipped (#186): native **"like" support** — `react_to_note` /
`unreact_to_note` in `api_client.py`, exposed as the manual `api_like.py` CLI.

Deferred: native **video** Note posting (mux upload pipeline, still Playwright;
tracked in #189).
