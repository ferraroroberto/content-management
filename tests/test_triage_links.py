"""Pure tests for newsletter.triage.gmail — link extraction, noise rules, local
redirect decoding, dedupe. No network, no Gmail, no Notion.

Run: `& .\\.venv\\Scripts\\python.exe -m unittest tests.test_triage_links -v`
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import gmail as gm  # noqa: E402


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


FIXTURE_HTML = f"""
<html><head><style>a {{ color: red }}</style></head><body>
<a href="https://59b90e10.click.convertkit-mail4.com/abc/def/{_b64('https://jamesclear.com/3-2-1/august-20-2026')}">
  3-2-1: What good judgement requires, how to direct your attention</a>
<a href="https://link.hbr.org/click/47115533.43303/{_b64('https://hbr.org/2026/08/is-your-organizational-culture-too-nice?utm_medium=email')}/x">
  Is Your Organizational Culture Too Nice?</a>
<a href="https://link.hbr.org/click/47115533.43303/{_b64('https://hbr.org/my-library/preferences')}/y">Manage email preferences</a>
<a href="https://substack.com/app-link/post?publication_id=6133698&post_id=211910543&utm_source=post-email-title">
  Everything in Tech Gets Faster. The Grid Doesn't.</a>
<a href="https://substack.com/app-link/post?publication_id=6133698&post_id=211910543&utm_source=substack"><img alt="cover" src="x.png"></a>
<a href="https://substack.com/redirect/2/{_b64(json.dumps({'e': 'https://metatrends.substack.com/subscribe?x=1'}))}">upgrade your subscription.</a>
<a href="https://substack.com/redirect/2/{_b64(json.dumps({'e': 'https://example.org/deep-dive-on-world-models'}))}">A functional taxonomy of world models</a>
<a href="https://email.mckinsey.com/capabilities/mckinsey-technology/our-insights/quantum-monitor-2026?stcr=1">
  McKinsey Quantum Technology Monitor 2026: A commercial tipping point</a>
<a href="https://link.mail.beehiiv.com/v2/c/deadbeef">Why you can't get rid of bad habits, explained</a>
<a href="https://link.mail.beehiiv.com/v2/c/cafebabe">share on facebook</a>
<a href="https://u25296327.ct.sendgrid.net/ls/click?upn=u001.abc">Share to LinkedIn</a>
<a href="https://insead.us2.list-manage.com/unsubscribe?u=1&id=2">unsubscribe</a>
<a href="mailto:someone@example.com">someone@example.com</a>
<a href="https://www.scotthyoung.com/blog/2026/08/12/some-article-slug/">Read this long article about learning faster</a>
<a href="https://x.com/intent/tweet?url=https%3A%2F%2Fexample.org">Share to Twitter X</a>
<a href="https://twitter.com/someone">@someone</a>
<a href="https://example.com/a.png">An image link with a long enough label</a>
<script>var a = "<a href='https://evil.example/script'>ignored</a>";</script>
</body></html>
"""


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.links = gm.links_from_html(FIXTURE_HTML)
        self.by_label = {l.label: l for l in self.links}

    def test_script_and_style_anchors_are_ignored(self) -> None:
        self.assertFalse(any("evil.example" in l.href for l in self.links))

    def test_convertkit_and_hbr_base64_decode_locally(self) -> None:
        jc = self.by_label["3-2-1: What good judgement requires, how to direct your attention"]
        self.assertEqual(jc.target, "https://jamesclear.com/3-2-1/august-20-2026")
        self.assertEqual(jc.via, "b64")
        hbr = self.by_label["Is Your Organizational Culture Too Nice?"]
        self.assertTrue(hbr.target.startswith("https://hbr.org/2026/08/is-your-organizational-culture-too-nice"))
        # canonical strips utm_*
        self.assertNotIn("utm_", hbr.canonical)

    def test_substack_redirect2_json_decodes_locally(self) -> None:
        link = self.by_label["A functional taxonomy of world models"]
        self.assertEqual(link.target, "https://example.org/deep-dive-on-world-models")
        self.assertEqual(link.via, "b64")

    def test_substack_post_anchors_dedupe_by_post_id(self) -> None:
        posts = [l for l in self.links if gm.substack_post_key(l)]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].label, "Everything in Tech Gets Faster. The Grid Doesn't.")
        self.assertFalse(posts[0].resolved)  # needs an HTTP hop

    def test_mckinsey_host_rewrite(self) -> None:
        link = self.by_label["McKinsey Quantum Technology Monitor 2026: A commercial tipping point"]
        self.assertTrue(link.target.startswith("https://www.mckinsey.com/capabilities/"))
        self.assertEqual(link.via, "host-rewrite")

    def test_direct_links_are_their_own_target(self) -> None:
        link = self.by_label["Read this long article about learning faster"]
        self.assertEqual(link.via, "direct")
        self.assertEqual(link.canonical, "https://scotthyoung.com/blog/2026/08/12/some-article-slug")

    def test_noise_is_dropped(self) -> None:
        labels = set(self.by_label)
        for noisy in ("Manage email preferences", "share on facebook", "Share to LinkedIn", "unsubscribe",
                      "someone@example.com", "upgrade your subscription.", "Share to Twitter X", "@someone",
                      "An image link with a long enough label"):
            self.assertNotIn(noisy, labels, noisy)

    def test_opaque_redirectors_stay_unresolved(self) -> None:
        link = self.by_label["Why you can't get rid of bad habits, explained"]
        self.assertFalse(link.resolved)
        self.assertEqual(link.via, "raw")
        self.assertTrue(gm.is_redirector(link.href))


class PublicationDomainTests(unittest.TestCase):
    def test_every_substack_publication_is_its_own_domain(self) -> None:
        self.assertEqual(gm.publication_domain("https://open.substack.com/pub/adamgrant/p/the-truth?utm_source=x"), "adamgrant.substack.com")
        self.assertEqual(gm.publication_domain("https://open.substack.com/pub/TheGoodBusy/p/slug"), "thegoodbusy.substack.com")
        # unresolved redirect / app-link → the sender's publication
        self.assertEqual(gm.publication_domain("https://substack.com/redirect/abc?j=x", "mikefisher@substack.com"), "mikefisher.substack.com")
        self.assertEqual(gm.publication_domain("https://substack.com/app-link/post?publication_id=1", "rishad+tag@substack.com"), "rishad.substack.com")
        # nothing to derive from → the shared host stays (still one bucket, but only for truly unknown links)
        self.assertEqual(gm.publication_domain("https://substack.com/redirect/abc", "someone@gmail.com"), "substack.com")
        # everything else: host without www.
        self.assertEqual(gm.publication_domain("https://www.hbr.org/2026/08/x?deliveryName=y", "x@hbr.org"), "hbr.org")
        self.assertEqual(gm.publication_domain("https://thegoodbusy.substack.com/p/x"), "thegoodbusy.substack.com")


class RuleTests(unittest.TestCase):
    def test_is_redirector_labels(self) -> None:
        self.assertTrue(gm.is_redirector("https://link.mail.beehiiv.com/v2/c/x"))
        self.assertTrue(gm.is_redirector("https://59b90e10.click.convertkit-mail4.com/a/b/c"))
        self.assertTrue(gm.is_redirector("https://substack.com/redirect/abc?j=x"))
        self.assertTrue(gm.is_redirector("https://insead.us2.list-manage.com/track/click?u=1"))
        self.assertFalse(gm.is_redirector("https://news.ycombinator.com/item?id=1"))
        self.assertFalse(gm.is_redirector("https://hbr.org/2026/08/some-article"))
        self.assertFalse(gm.is_redirector("https://open.substack.com/pub/x/p/y"))

    def test_canonical_substack(self) -> None:
        self.assertEqual(gm.canonical_substack("https://open.substack.com/pub/metatrends/p/everything?utm=1"),
                         "https://metatrends.substack.com/p/everything")
        self.assertEqual(gm.canonical_substack("https://nesslabs.com/x"), "https://nesslabs.com/x")

    def test_short_anchor_threshold(self) -> None:
        link = gm.Link(href="https://example.org/some/path", text="Accountability.")
        gm.classify_noise(link, min_anchor_chars=12)
        self.assertFalse(link.noise)
        link2 = gm.Link(href="https://example.org/some/path", text="Blog")
        gm.classify_noise(link2, min_anchor_chars=12)
        self.assertTrue(link2.noise)
        self.assertEqual(link2.noise_reason, "short-anchor")

    def test_plain_text_fallback(self) -> None:
        links = gm.extract_links("", "see https://example.org/article-one. and https://example.org/two")
        self.assertEqual([l.href for l in links], ["https://example.org/article-one", "https://example.org/two"])

    def test_message_html_skips_attachments(self) -> None:
        body = base64.urlsafe_b64encode(b"<p>hi</p>").decode()
        raw = {"payload": {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "text/html", "body": {"data": body}},
            {"mimeType": "text/html", "filename": "attached.html", "body": {"data": body}},
            {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"hi").decode()}},
        ]}}
        html, plain = gm.message_html(raw)
        self.assertEqual(html, "<p>hi</p>")
        self.assertEqual(plain, "hi")

    def test_redirect_cache_roundtrip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "r.json"
            cache = gm.RedirectCache(path)
            cache.put("k", "https://final.example/")
            cache.put("f", "")
            cache.flush()
            again = gm.RedirectCache(path)
            self.assertEqual(again.get("k"), "https://final.example/")
            self.assertEqual(again.get("f"), "")
            self.assertIsNone(again.get("missing"))


class _StubResp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
    def close(self): pass


class _StubSession:
    """Scripted redirect chains keyed by (method, url)."""
    def __init__(self, table): self.table, self.calls = table, []
    def request(self, method, url, **kw):
        self.calls.append((method, url))
        assert kw.get("allow_redirects") is False and kw.get("stream") is True
        return self.table.get((method, url)) or self.table.get(("*", url)) or _StubResp(404)


class ResolveTests(unittest.TestCase):
    def test_manual_redirect_following_never_reads_bodies(self) -> None:
        sess = _StubSession({
            ("*", "https://link.mail.beehiiv.com/v2/c/abc"): _StubResp(302, "https://pub.beehiiv.com/p/post?x=1"),
            ("*", "https://pub.beehiiv.com/p/post?x=1"): _StubResp(200),
        })
        final, status = gm.resolve_one(sess, "https://link.mail.beehiiv.com/v2/c/abc", timeout=1)
        self.assertEqual((final, status), ("https://pub.beehiiv.com/p/post?x=1", "http"))
        self.assertEqual([m for m, _ in sess.calls], ["HEAD", "HEAD"])

    def test_head_refused_falls_back_to_get(self) -> None:
        sess = _StubSession({
            ("HEAD", "https://t.e2ma.net/click/a/b/c"): _StubResp(404),
            ("GET", "https://t.e2ma.net/click/a/b/c"): _StubResp(302, "/relative/path"),
            ("GET", "https://t.e2ma.net/relative/path"): _StubResp(200),
        })
        final, status = gm.resolve_one(sess, "https://t.e2ma.net/click/a/b/c", timeout=1)
        self.assertEqual((final, status), ("https://t.e2ma.net/relative/path", "http"))

    def test_redirect_loop_is_bounded(self) -> None:
        sess = _StubSession({("*", "https://go.example.com/x"): _StubResp(302, "https://go.example.com/x")})
        final, status = gm.resolve_one(sess, "https://go.example.com/x", timeout=1)
        self.assertEqual(status, "http-fail")
        self.assertLessEqual(len(sess.calls), 2 * gm._MAX_HOPS)

    def test_self_answering_url_is_its_own_target(self) -> None:
        sess = _StubSession({("*", "https://news.ycombinator.com/item?id=1"): _StubResp(200)})
        final, status = gm.resolve_one(sess, "https://news.ycombinator.com/item?id=1", timeout=1)
        self.assertEqual((final, status), ("https://news.ycombinator.com/item?id=1", "http"))


if __name__ == "__main__":
    unittest.main()
