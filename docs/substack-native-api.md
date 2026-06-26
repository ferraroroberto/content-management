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
| Edit draft | `PUT /<pub>/drafts/{id}` | |
| Pre-publish validate | `GET /<pub>/drafts/{id}/prepublish` | Returns `{errors, suggestions}`; does not publish. |
| Publish | `POST /<pub>/drafts/{id}/publish` | **Irreversible** — emails the whole list. Gated behind explicit `--confirm`; never in the cron. |

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

## Known fragility (be honest)

- Endpoints are undocumented and can change without notice. Concrete example: the
  `python-substack` helper `get_publication_subscriber_count` is already stale —
  it reads a `subscriberCount` key the endpoint no longer returns (it now returns
  a `subscribers` *list*). We don't rely on that helper.
- The library's `get_published_posts` returns the raw envelope; callers must
  unwrap `["posts"]` (`SubstackAPI.list_published` does this).
- Notes (the daily Note posting + note-engagement scrape) use a different endpoint
  family that the spike did **not** reverse-engineer; those stay on Playwright for
  now.
- No rate limiting was observed across the spike's calls, but it is unmeasured at
  daily-cron scale.

## Scope today vs. follow-ups

Shipped: native follower count (daily default) + manual archive pull + manual
draft create/edit/prepublish/publish. Deferred: wiring create/publish to the
Notion editorial database; migrating Note posting + note-engagement off Playwright;
"like" support. Tracked on issue #91.
