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

import json
import logging
import os
import tempfile
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
    "load_session",
    "fetch_follower_count",
    "SubstackAPI",
    "SESSION_FILE",
]


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
