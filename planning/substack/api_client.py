"""Native Substack HTTP API client (cookie-auth).

Talks to Substack's private (reverse-engineered) endpoints over HTTP, using the
session cookie harvested from the dedicated Chrome profile by
:mod:`planning.substack.extract_session`. This is a lighter, more robust path
than the Playwright automation: no DOM selectors, no reCAPTCHA, no shared-profile
lock, no headed-browser launch per run.

This module is the single source for the native path:

* :func:`load_session` — build an authenticated ``requests.Session`` from the
  cached cookies + User-Agent.
* :func:`fetch_follower_count` — the daily follower number (``followerCount``
  from ``/user/profile/self``); this is what the reporting pipeline uses when
  ``substack_profile.source == "native"``.
* :class:`SubstackAPI` — pull/archive + draft create/edit/publish, built on the
  ``python-substack`` library (which owns the publication resolution and the
  ProseMirror body builder).

The Playwright integration (``reporting/scrape_client/substack.py`` and the rest
of ``planning/substack/``) is intentionally **kept** as an alternative ``source``
— this module does not remove or modify it.

Cookie lifetime: ``substack.sid`` lives ~89 days, so the harvest step is a
once-per-quarter chore (the same cadence as the Playwright ``bootstrap_session``
re-login). When the cookie expires the API returns 401/403 and the helpers raise
:class:`SessionExpiredError` telling the operator to re-run ``extract_session``.

SECURITY: ``api_session.json`` holds live auth cookies — it is gitignored and
must never be committed (public repo).
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests

from substack import Api
from substack.post import Post, parse_inline

PACKAGE_DIR = Path(__file__).resolve().parent
SESSION_FILE = PACKAGE_DIR / "api_session.json"
BASE_URL = "https://substack.com/api/v1"

# Fallback only — the real UA that solved Cloudflare's challenge is stored in the
# session file and paired with cf_clearance; this is used if that is absent.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

logger = logging.getLogger("substack_api")

__all__ = [
    "SessionExpiredError",
    "VideoTranscodeError",
    "load_session",
    "fetch_follower_count",
    "fetch_own_notes",
    "fetch_self_handle",
    "build_note_body_json",
    "note_permalink",
    "publish_note",
    "delete_note",
    "react_to_note",
    "unreact_to_note",
    "build_section_nodes",
    "SubstackAPI",
    "SESSION_FILE",
]

# The profile feed pages at 12 items; cap the cursor walk so a pagination
# change can never spin forever.
NOTES_PAGE_CAP = 12

# Who may reply to a published Note. "everyone" is what the web composer sends.
NOTE_REPLY_ROLE = "everyone"


class SessionExpiredError(RuntimeError):
    """Raised when the cached Substack cookie is missing or rejected (401/403).

    The fix is always the same: re-run ``python -m planning.substack.extract_session``.
    """


def load_session(session_file: Path = SESSION_FILE) -> tuple[requests.Session, dict]:
    """Build an authenticated ``requests.Session`` from the cached cookies + UA."""
    if not session_file.exists():
        raise SessionExpiredError(
            f"No Substack API session at {session_file}. Run "
            "`python -m planning.substack.extract_session` to harvest the cookie."
        )
    meta = json.loads(session_file.read_text(encoding="utf-8"))
    cookies = meta.get("cookies") or {}
    if "substack.sid" not in cookies:
        raise SessionExpiredError(
            "Cached session is missing 'substack.sid' — re-run "
            "`python -m planning.substack.extract_session`."
        )
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({"User-Agent": meta.get("user_agent") or DEFAULT_UA})
    return session, meta


def _check(resp: requests.Response) -> requests.Response:
    """Raise :class:`SessionExpiredError` on auth failure, else ``raise_for_status``."""
    if resp.status_code in (401, 403):
        raise SessionExpiredError(
            f"Substack API auth failed ({resp.status_code}) — the session cookie "
            "likely expired. Re-run `python -m planning.substack.extract_session`."
        )
    resp.raise_for_status()
    return resp


def fetch_follower_count(session_file: Path = SESSION_FILE) -> int:
    """Return the profile follower count (the daily number).

    Equivalent to the Playwright scrape of "Total followers (N)" but via a single
    authenticated GET. ``followerCount`` is the same integer that page renders.
    """
    session, _ = load_session(session_file)
    resp = _check(session.get(f"{BASE_URL}/user/profile/self", timeout=30))
    data = resp.json()
    count = data.get("followerCount")
    if not isinstance(count, int):
        raise RuntimeError(
            "Unexpected /user/profile/self shape — no integer 'followerCount' "
            f"(got {type(count).__name__}). Endpoint may have changed."
        )
    return count


def _fetch_self(session: requests.Session) -> dict:
    """``/user/profile/self`` with the two fields we depend on validated."""
    data = _check(session.get(f"{BASE_URL}/user/profile/self", timeout=30)).json()
    if not data.get("id") or not data.get("handle"):
        raise RuntimeError(
            "Unexpected /user/profile/self shape — no 'id'/'handle'. "
            "Endpoint may have changed."
        )
    return data


def fetch_self_handle(*, session_file: Path = SESSION_FILE) -> str:
    """Our own handle, straight from the API.

    Authoritative fallback for building note permalinks: the note-create
    response does **not** echo the handle, and this is the same source
    ``fetch_own_notes`` uses, so permalinks written at publish time and
    permalinks read back by the reporting pipeline cannot diverge.
    """
    session, _ = load_session(session_file)
    return _fetch_self(session)["handle"]


def fetch_own_notes(
    limit: int = 20, *, session_file: Path = SESSION_FILE
) -> tuple[str, list[dict]]:
    """Return ``(handle, comments)`` — our own published Notes, newest first.

    Walks ``/reader/feed/profile/{user_id}?types[]=note``, following ``nextCursor``
    until ``limit`` notes are collected or the feed is exhausted. Each element is
    the raw ``comment`` payload (a Note is a ``comment`` of ``type == "feed"``);
    :func:`reporting.scrape_client.substack_native.note_record` maps it to the
    reporting record shape.

    The handle comes from ``/user/profile/self`` rather than ``config.json`` so
    the permalinks we build are guaranteed to match the account the cookie
    belongs to.

    Equivalent to the Playwright feed-walk + per-note permalink visit, but as
    ``1 + ceil(limit/12)`` GETs instead of one browser launch and ~12 page loads.
    """
    session, _ = load_session(session_file)
    self_data = _fetch_self(session)
    user_id = self_data["id"]
    handle = self_data["handle"]

    url = f"{BASE_URL}/reader/feed/profile/{user_id}?types%5B%5D=note"
    comments: list[dict] = []
    cursor: Optional[str] = None
    for _ in range(NOTES_PAGE_CAP):
        page_url = url if cursor is None else f"{url}&cursor={cursor}"
        data = _check(session.get(page_url, timeout=30)).json()
        for item in data.get("items") or []:
            comment = item.get("comment")
            if comment:
                comments.append(comment)
        if len(comments) >= limit:
            break
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return handle, comments[:limit]


def note_permalink(handle: str, note_id) -> str:
    """The public URL of a Note — the same value the editorial ``post_url`` holds."""
    return f"https://substack.com/@{handle}/note/c-{note_id}"


def build_note_body_json(text: str) -> dict:
    """Build a Note's ProseMirror ``bodyJson`` from plain text.

    Blank-line-separated blocks become separate ``paragraph`` nodes, which is
    exactly how Substack's own composer stores a multi-paragraph Note (verified
    against published notes' stored ``body_json``).

    Text is inserted **literally** — no markdown parsing — for the same reason
    the newsletter's section builder does it: the body comes from a Notion
    column and may legitimately contain ``*``, ``[`` or backticks.

    Pure (no session, no network) so it can be unit-tested on its own.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", (text or "").strip()) if b.strip()]
    if not blocks:
        raise ValueError("Refusing to build an empty Note body.")
    return {
        "type": "doc",
        "attrs": {"schemaVersion": "v1", "title": None},
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": block}]}
            for block in blocks
        ],
    }


def _image_data_uri(image_path: Path) -> str:
    """Read an image file into the ``data:<mime>;base64,…`` form the API expects."""
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _upload_note_image(session: requests.Session, image_path: Path) -> str:
    """Upload an image and return its CDN URL (step 1 of the attachment flow)."""
    resp = _check(session.post(
        f"{BASE_URL}/image",
        json={"image": _image_data_uri(image_path)},
        timeout=120,
    ))
    url = (resp.json() or {}).get("url")
    if not url:
        raise RuntimeError("Substack /image returned no 'url' — endpoint may have changed.")
    return url


def _create_note_attachment(session: requests.Session, image_url: str) -> str:
    """Turn an uploaded image URL into a Note attachment id (step 2)."""
    resp = _check(session.post(
        f"{BASE_URL}/comment/attachment",
        json={"url": image_url, "type": "image"},
        timeout=60,
    ))
    attachment_id = (resp.json() or {}).get("id")
    if not attachment_id:
        raise RuntimeError(
            "Substack /comment/attachment returned no 'id' — endpoint may have changed."
        )
    return attachment_id


class VideoTranscodeError(RuntimeError):
    """Raised when Substack's Mux transcode of an uploaded video fails or times out."""


# A part every 50MB (server-observed, issue #189 — parts scale as
# ceil(fileSize / 50_000_000)); S3 multipart only requires order-preserving,
# >=5MB-except-last parts, so slicing into exactly as many equal parts as the
# server hands back URLs for is correct regardless of the server's own
# internal chunk-size convention (verified live with a real 2-part upload).
VIDEO_PART_UPLOAD_TIMEOUT_S = 180
VIDEO_TRANSCODE_POLL_INTERVAL_S = 3
VIDEO_TRANSCODE_TIMEOUT_S = 300


def _upload_video(session: requests.Session, video_path: Path, duration_seconds: float) -> str:
    """Upload a video through Substack's chunked multipart pipeline and kick off
    the Mux transcode. Returns the ``media_upload_id``.

    Mirrors what the real composer does (issue #189), captured by driving it
    with a network listener attached (the write routes are lazily-imported
    webpack chunks, invisible to a bundle grep — same lesson as #185):

    1. ``POST /video/upload?filetype=&fileSize=&fileName=`` — no body; the
       response hands back a ready-to-use ``media_upload_id`` plus one
       presigned S3 PUT URL per part (no client-side AWS signing needed).
    2. ``PUT`` each part directly to its presigned URL, collecting the
       ``ETag`` response header per part.
    3. ``POST /video/upload/{id}/transcode`` with the source duration, the
       multipart upload id, and the collected ETags — this both completes
       the S3 multipart upload and kicks off Mux transcoding.
    """
    size = video_path.stat().st_size
    mime = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    resp = _check(session.post(
        f"{BASE_URL}/video/upload",
        params={"filetype": mime, "fileSize": size, "fileName": video_path.name},
        timeout=60,
    ))
    data = resp.json() or {}
    media_upload_id = (data.get("mediaUpload") or {}).get("id")
    multipart_upload_id = data.get("multipartUploadId")
    urls = data.get("multipartUploadUrls") or []
    if not media_upload_id or not multipart_upload_id or not urls:
        raise RuntimeError(
            "Substack /video/upload returned no media upload id / part URLs — "
            "endpoint may have changed."
        )

    file_bytes = video_path.read_bytes()
    n_parts = len(urls)
    part_size = -(-len(file_bytes) // n_parts)  # ceil division
    etags = []
    for i, part_url in enumerate(urls):
        chunk = file_bytes[i * part_size:(i + 1) * part_size]
        put_resp = session.put(part_url, data=chunk, timeout=VIDEO_PART_UPLOAD_TIMEOUT_S)
        put_resp.raise_for_status()
        etag = put_resp.headers.get("ETag")
        if not etag:
            raise RuntimeError(f"S3 part {i + 1}/{n_parts} upload returned no ETag header.")
        etags.append(etag)

    _check(session.post(
        f"{BASE_URL}/video/upload/{media_upload_id}/transcode",
        json={
            "duration": duration_seconds,
            "multipart_upload_id": multipart_upload_id,
            "multipart_upload_etags": etags,
        },
        timeout=60,
    ))
    return media_upload_id


def _wait_for_video_transcode(
    session: requests.Session,
    media_upload_id: str,
    *,
    timeout_s: int = VIDEO_TRANSCODE_TIMEOUT_S,
) -> None:
    """Poll ``GET /video/upload/{id}`` until Mux reports ``state == "transcoded"``."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = _check(session.get(f"{BASE_URL}/video/upload/{media_upload_id}", timeout=30)).json()
        state = data.get("state")
        if state == "transcoded":
            return
        if state in ("errored", "failed"):
            raise VideoTranscodeError(
                f"Substack video transcode failed (state={state!r}) for media_upload_id={media_upload_id}."
            )
        time.sleep(VIDEO_TRANSCODE_POLL_INTERVAL_S)
    raise VideoTranscodeError(
        f"Video {media_upload_id} did not reach 'transcoded' within {timeout_s}s."
    )


def _create_video_attachment(session: requests.Session, media_upload_id: str) -> str:
    """Turn a transcoded media upload into a Note attachment id."""
    resp = _check(session.post(
        f"{BASE_URL}/comment/attachment",
        json={"mediaUploadId": media_upload_id, "type": "video"},
        timeout=30,
    ))
    attachment_id = (resp.json() or {}).get("id")
    if not attachment_id:
        raise RuntimeError(
            "Substack /comment/attachment (video) returned no 'id' — endpoint may have changed."
        )
    return attachment_id


def publish_note(
    text: str,
    *,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    video_duration_seconds: Optional[float] = None,
    session_file: Path = SESSION_FILE,
) -> dict:
    """Publish a Substack Note over the native API. Returns the created note dict.

    **This is immediately public** — a Note has no draft state, so there is no
    dry-run at this layer; callers own that gate (``post_substack_note.py``/
    ``post_substack_video_note.py`` short-circuit before reaching here).

    A Note is a ``comment`` of ``type == "feed"``. With an image it is three
    calls, mirroring exactly what the web composer sends:

    1. ``POST /image`` — the file as a base64 data URI → a CDN URL.
    2. ``POST /comment/attachment`` — that URL → an attachment id.
    3. ``POST /comment/feed`` — ``bodyJson`` + ``attachmentIds``.

    With a video, the attachment step is a chunked multipart upload + Mux
    transcode instead (issue #189) — see :func:`_upload_video`. Pass exactly
    one of ``image_path``/``video_path``; ``video_path`` requires
    ``video_duration_seconds`` (the transcode call needs it up front, before
    Mux has produced anything to derive it from — get it via
    ``planning.videos.videos_session.probe_duration_seconds``).

    Unlike the Playwright path this returns the created note's **own** id, so
    the permalink is exact rather than "whatever is topmost on the profile a
    moment later".
    """
    if image_path and video_path:
        raise ValueError("publish_note: pass image_path or video_path, not both.")
    if video_path and video_duration_seconds is None:
        raise ValueError("publish_note: video_path requires video_duration_seconds.")

    body_json = build_note_body_json(text)  # validate before any network call
    session, _ = load_session(session_file)

    payload: dict = {"bodyJson": body_json, "replyMinimumRole": NOTE_REPLY_ROLE}
    if image_path:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Note image not found: {image_path}")
        logger.info("📤 Uploading note image: %s", image_path.name)
        payload["attachmentIds"] = [
            _create_note_attachment(session, _upload_note_image(session, image_path))
        ]
    elif video_path:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Note video not found: {video_path}")
        logger.info("📤 Uploading note video: %s (%.1fs)", video_path.name, video_duration_seconds)
        media_upload_id = _upload_video(session, video_path, video_duration_seconds)
        _wait_for_video_transcode(session, media_upload_id)
        payload["attachmentIds"] = [_create_video_attachment(session, media_upload_id)]

    resp = _check(session.post(f"{BASE_URL}/comment/feed", json=payload, timeout=60))
    data = resp.json() or {}
    note = data.get("comment") if isinstance(data.get("comment"), dict) else data
    if not note.get("id"):
        raise RuntimeError(
            f"Note POST succeeded but no id in the response (keys: {sorted(data)}) — "
            "endpoint may have changed."
        )
    return note


def delete_note(note_id, *, session_file: Path = SESSION_FILE) -> None:
    """Delete a published Note. Used to clean up throwaway verification notes."""
    session, _ = load_session(session_file)
    _check(session.delete(f"{BASE_URL}/comment/{note_id}", timeout=30))


# The heart is the only reaction the web composer sends — Notes have no other
# reaction type (verified live, issue #186).
NOTE_REACTION_HEART = "❤"


def react_to_note(note_id, *, session_file: Path = SESSION_FILE) -> None:
    """React ("like") to a Note. Idempotent — reacting twice leaves the count
    unchanged (verified live: two consecutive POSTs both return 200, count
    stays at 1)."""
    session, _ = load_session(session_file)
    _check(session.post(
        f"{BASE_URL}/comment/{note_id}/reaction",
        json={"reaction": NOTE_REACTION_HEART},
        timeout=30,
    ))


def unreact_to_note(note_id, *, session_file: Path = SESSION_FILE) -> None:
    """Remove a reaction from a Note. Idempotent — un-reacting when not
    reacted is a no-op (verified live, same as the double-POST case)."""
    session, _ = load_session(session_file)
    _check(session.delete(
        f"{BASE_URL}/comment/{note_id}/reaction",
        json={"reaction": NOTE_REACTION_HEART},
        timeout=30,
    ))


def _text_node(text: str, href: Optional[str] = None) -> dict:
    """One ProseMirror text node, optionally carrying a link mark."""
    node: dict = {"type": "text", "text": text}
    if href:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


def build_section_nodes(
    sections: list[tuple[str, list[tuple[str, str]]]],
    *,
    intro: Optional[str] = None,
) -> list[dict]:
    """Build the ProseMirror body nodes for a sectioned edition.

    ``sections`` is ``[(heading, [(link_text, url), ...]), ...]`` — one heading
    per topic, one linked bullet per article. ``intro``, when given, becomes the
    first paragraph.

    Text is emitted as **literal** text nodes rather than parsed as markdown:
    article titles come from arbitrary web pages and routinely contain ``[``,
    ``*`` or backticks, which markdown parsing would silently mangle.

    Pure (no session, no network) so it can be unit-tested on its own.

    NOTE: the library's ``Post.from_markdown`` cannot express this shape — it
    folds bullet lines that follow a heading into the heading's own text node,
    destroying the links. Hence the explicit node construction here.
    """
    nodes: list[dict] = []
    if intro:
        nodes.append({"type": "paragraph", "content": [_text_node(intro)]})
    for heading, articles in sections:
        nodes.append(
            {
                "type": "heading",
                "content": [_text_node(heading)],
                "attrs": {"level": 2},
            }
        )
        items = [
            {
                "type": "list_item",
                "content": [{"type": "paragraph", "content": [_text_node(name, url)]}],
            }
            for name, url in articles
        ]
        # An empty bullet_list is not valid ProseMirror — emit the heading alone
        # for a topic that collected no articles.
        if items:
            nodes.append({"type": "bullet_list", "content": items})
    return nodes


class SubstackAPI:
    """Authenticated wrapper over ``python-substack`` for pull + write.

    Used by the manual archive/create CLIs (not the daily cron). The follower
    count does *not* go through here — it uses :func:`fetch_follower_count` to
    avoid the publication-resolution round-trips the library does at construction.
    """

    def __init__(
        self,
        publication_url: Optional[str] = None,
        *,
        session_file: Path = SESSION_FILE,
    ) -> None:
        _, meta = load_session(session_file)
        cookies = meta.get("cookies") or {}
        user_agent = meta.get("user_agent") or DEFAULT_UA

        # python-substack authenticates from a {name: value} cookies *file*.
        # Write a throwaway temp file (auth happens at construction, then the
        # cookies live in the library's own session, so the file can go away).
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(cookies, tmp)
            tmp.close()
            self._api = Api(cookies_path=tmp.name, publication_url=publication_url)
        finally:
            os.unlink(tmp.name)

        # Present the same browser UA on every request (Cloudflare + heuristics).
        self._api._session.headers.update({"User-Agent": user_agent})
        self.user_id = self._api.get_user_id()

    # ---- pull / archive -------------------------------------------------

    def list_published(self, limit: int = 50) -> list[dict]:
        """Return published posts (newest first). Unwraps the ``{posts: [...]}`` envelope."""
        resp = self._api.get_published_posts(limit=limit)
        posts = resp.get("posts", resp) if isinstance(resp, dict) else resp
        return posts if isinstance(posts, list) else []

    def build_archive(self, limit: int = 50, with_body: bool = False) -> list[dict]:
        """Build an archive of published posts.

        ``with_body=True`` fetches each post's full body (one extra GET per post
        via ``/posts/by-id/{id}``) — the list endpoint omits the body.
        """
        archive: list[dict] = []
        for post in self.list_published(limit=limit):
            entry = {
                "id": post.get("id"),
                "uuid": post.get("uuid"),
                "title": post.get("title"),
                "slug": post.get("slug"),
                "post_date": post.get("post_date"),
                "audience": post.get("audience"),
                "type": post.get("type"),
            }
            if with_body:
                full = self._get_post_by_id(post.get("id"))
                entry["canonical_url"] = full.get("canonical_url")
                entry["body_html"] = full.get("body_html")
            archive.append(entry)
        return archive

    def _get_post_by_id(self, post_id: int) -> dict:
        resp = self._api._session.get(
            f"{self._api.publication_url}/posts/by-id/{post_id}", timeout=30
        )
        _check(resp)
        body = resp.json()
        return body.get("post", body) if isinstance(body, dict) else {}

    # ---- create / edit / publish ---------------------------------------

    def create_draft(
        self,
        title: str,
        subtitle: str,
        paragraphs: list[str],
        *,
        heading: Optional[str] = None,
        image_path: Optional[str] = None,
        audience: str = "everyone",
    ) -> dict:
        """Create a newsletter edition as a DRAFT (private — not sent to anyone).

        ``paragraphs`` support inline markdown (``**bold**``, ``[text](url)`` …).
        Returns the created draft dict (carries ``id``).
        """
        post = Post(title, subtitle, self.user_id, audience=audience)
        if heading:
            post.heading(heading, level=2)
        for para in paragraphs:
            post.add({"type": "paragraph", "content": parse_inline(para)})
        if image_path:
            uploaded = self._api.get_image(image_path)
            url = uploaded.get("url")
            if url:
                post.add({"type": "captionedImage", "src": url})
        return self._api.post_draft(post.get_draft())

    def create_draft_from_sections(
        self,
        title: str,
        subtitle: str,
        sections: list[tuple[str, list[tuple[str, str]]]],
        *,
        intro: Optional[str] = None,
        audience: str = "everyone",
    ) -> dict:
        """Create a newsletter edition DRAFT laid out as headed link sections.

        The weekly-newsletter counterpart to :meth:`create_draft` (which is
        paragraph-shaped and drives the manual ``api_create`` CLI). Private —
        emails no one. Returns the created draft dict (carries ``id``).

        See :func:`build_section_nodes` for the body shape and why the nodes are
        built explicitly rather than through the library's markdown path.
        """
        post = Post(title, subtitle, self.user_id, audience=audience)
        post.draft_body["content"].extend(build_section_nodes(sections, intro=intro))
        return self._api.post_draft(post.get_draft())

    def update_draft(self, draft_id: int, **fields) -> dict:
        """Edit an existing draft (e.g. ``draft_subtitle=...``)."""
        return self._api.put_draft(draft_id, **fields)

    def prepublish(self, draft_id: int) -> dict:
        """Run Substack's own pre-publish validation. Does NOT publish."""
        return self._api.prepublish_draft(draft_id)

    def publish(self, draft_id: int, *, send: bool = True) -> dict:
        """Publish a draft. IRREVERSIBLE: ``send=True`` emails subscribers.

        Callers must gate this behind an explicit human confirmation — it is
        never invoked by the daily pipeline.
        """
        return self._api.publish_draft(draft_id, send=send)

    def delete_draft(self, draft_id: int) -> dict:
        """Delete a draft (used to clean up throwaway validation drafts)."""
        return self._api.delete_draft(draft_id)
