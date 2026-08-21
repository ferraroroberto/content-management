"""Pure tests for the triage engine: ranking rules, report ↔ feedback round-trip,
priors, paywall detection, LLM JSON parsing. No network, no hub, no Gmail."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import feedback as fb  # noqa: E402
from newsletter.triage import fetch as fx  # noqa: E402
from newsletter.triage import rank as rk  # noqa: E402
from newsletter.triage import report as rp  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402

RULES = {"caps": {"hbr_per_edition": {"target": 3}, "same_author_per_edition": {"target": 2},
                  "same_domain_per_edition": {"target": 3}},
         "edition": {"must_read_topic_prior": {"personal development": 0.6, "leadership and management": 0.35, "innovation": 0.04}}}


def _cand(i: int, *, topic: str, score: float, domain: str = "example.com", sender: str = "a@x.com",
          author: str = "", msg: str = None, **kw) -> rk.Candidate:
    c = rk.Candidate(cid=f"m{i}:0", message_id=msg or f"m{i}", sender_name=sender.split("@")[0], sender_address=sender,
                     subject="s", email_ts=f"2026-08-{10 + (i % 10):02d}T10:00:00+00:00", label=f"Article {i}",
                     url=f"https://{domain}/a{i}", canonical=f"https://{domain}/a{i}", domain=domain, author=author or None,
                     score=score, topic=topic, **kw)
    if score is not None:
        c.content = sc.ContentScore(topic=topic, relevance=int(min(5, score / 2)), star=i % 6, ok=True, reason="fits")
    return c


class RankTests(unittest.TestCase):
    def test_caps_and_fill(self) -> None:
        cands = []
        for i in range(6):   # 6 HBR leadership → only 3 allowed
            cands.append(_cand(i, topic="leadership and management", score=9 - i * 0.1, domain="hbr.org", sender="hbr@x.com"))
        for i in range(10, 20):  # 10 other leadership, 3 from the same author
            cands.append(_cand(i, topic="leadership and management", score=8 - i * 0.1, domain=f"d{i}.com",
                               sender=f"s{i}@x.com", author="Same Person" if i < 13 else ""))
        for i in range(30, 40):
            cands.append(_cand(i, topic="personal development", score=7 - i * 0.05, domain=f"p{i}.com", sender=f"p{i}@x.com"))
        sel = rk.select(cands, RULES)
        lead = sel.picks["leadership and management"]
        self.assertEqual(len(lead), 8)
        self.assertEqual(sum(1 for c in lead if c.domain == "hbr.org"), 3)
        self.assertEqual(sum(1 for c in lead if c.author == "Same Person"), 2)
        self.assertEqual(len(sel.picks["personal development"]), 8)
        self.assertEqual(sel.short.get("innovation"), 8)
        self.assertIsNotNone(sel.stars["leadership and management"])
        self.assertIs(sel.must_read.topic, "personal development")  # prior 0.6 × 7 > 0.35 × 9

    def test_vetoes_and_states(self) -> None:
        pay = _cand(1, topic="innovation", score=9.0, paywalled=True)
        never = _cand(2, topic="innovation", score=9.0, sender_weight=0.0, sender_basis="override:never")
        dup = _cand(3, topic="innovation", score=9.0, in_notion=True)
        unknown = _cand(4, topic="innovation", score=None)
        low = _cand(5, topic="innovation", score=1.0)
        promo = _cand(6, topic="innovation", score=8.0)
        promo.content.promo = True
        good = _cand(7, topic="innovation", score=6.0)
        sel = rk.select([pay, never, dup, unknown, low, promo, good], RULES)
        self.assertEqual([c.verdict for c in (pay, never, dup, unknown, low, promo, good)],
                         ["vetoed", "vetoed", "duplicate", "unknown", "low", "vetoed", "selected"])
        self.assertEqual(pay.reason, "paywalled")
        self.assertEqual(sel.picks["innovation"], [good])

    def test_same_article_different_tracking_params_dedupes(self) -> None:
        a = _cand(1, topic="innovation", score=9.0, domain="hbr.org", sender="a@x.com")
        b = _cand(2, topic="innovation", score=8.0, domain="hbr.org", sender="b@x.com")
        a.url = a.canonical = "https://hbr.org/2026/08/article?deliveryName=NL_1"
        b.url = b.canonical = "https://hbr.org/2026/08/article?deliveryName=NL_2"
        sel = rk.select([a, b], RULES)
        self.assertEqual((a.verdict, b.verdict), ("selected", "duplicate"))

    def test_slug_title_when_label_is_a_url(self) -> None:
        c = _cand(1, topic="innovation", score=9.0)
        c.title, c.label = "", "https://rishad.substack.com/p/from-caterpillar-to-butterfly?utm_source=x"
        c.url = c.label
        self.assertEqual(c.display_title, "From caterpillar to butterfly")
        self.assertEqual(rk.slug_title("https://x.com/abc"), "https://x.com/abc")

    def test_one_pick_per_email_except_digests(self) -> None:
        a = _cand(1, topic="innovation", score=9.0, msg="same", sender="x@y.com")
        b = _cand(2, topic="innovation", score=8.0, msg="same", sender="x@y.com")
        r1 = _cand(3, topic="innovation", score=9.0, msg="rw", sender="hello@readwise.io")
        r2 = _cand(4, topic="innovation", score=8.0, msg="rw", sender="hello@readwise.io")
        sel = rk.select([a, b, r1, r2], RULES)
        self.assertEqual(a.verdict, "selected")
        self.assertEqual(b.verdict, "runner-up")
        self.assertEqual((r1.verdict, r2.verdict), ("selected", "selected"))


class PriorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pr = sc.Priors({
            "sender_overrides": {"pay@wall.com": {"tier": "never"}, "fav@x.com": {"tier": "always"}},
            "sender_priors": {"ranked": [{"address": "hit@x.com", "hit_rate_per_email": 0.9, "topics": {"innovation": 5}}],
                              "floor": [{"address": "alumni@x.edu"}]},
            "domain_priors": [{"domain": "hbr.org", "picks": 150, "topics": {"leadership and management": 120}}],
        })

    def test_weights(self) -> None:
        self.assertEqual(self.pr.sender("pay@wall.com")[0], 0.0)
        self.assertEqual(self.pr.sender("FAV@x.com")[0], 1.5)
        w, basis, new = self.pr.sender("hit@x.com")
        self.assertAlmostEqual(w, 1.41, places=2)
        self.assertFalse(new)
        self.assertEqual(self.pr.sender("alumni@x.edu")[0], sc.FLOOR_WEIGHT)
        w, basis, new = self.pr.sender("someone@new.com")
        self.assertTrue(new)
        self.assertEqual(basis, "new sender")
        self.assertGreater(self.pr.domain("hbr.org")[0], 0)
        self.assertEqual(self.pr.topic_prior("hit@x.com", "hbr.org"), "innovation")
        self.assertEqual(self.pr.topic_prior("nobody@x.com", "hbr.org"), "leadership and management")

    def test_combine(self) -> None:
        m = sc.MetaScore(topic="innovation", fit=4, ok=True)
        self.assertAlmostEqual(sc.combine(sender_weight=1.0, domain_bonus=0.0, meta=m, content=None), 6.8, places=1)
        c = sc.ContentScore(topic="innovation", relevance=5, star=0, news=True, ok=True)
        self.assertAlmostEqual(sc.combine(sender_weight=1.0, domain_bonus=0.0, meta=m, content=c), 6.0, places=1)
        self.assertIsNone(sc.combine(sender_weight=1.0, domain_bonus=0.0, meta=None, content=None))

    def test_json_parsing(self) -> None:
        self.assertEqual(sc._parse_json('```json\n[{"i": 1}]\n```'), [{"i": 1}])
        self.assertEqual(sc._parse_json('Sure! {"topic": "innovation"} done'), {"topic": "innovation"})
        self.assertIsNone(sc._parse_json("no json here"))
        self.assertEqual(sc._topic("Leadership & Management"), "leadership and management")
        self.assertEqual(sc._topic("Personal Development"), "personal development")


class PaywallTests(unittest.TestCase):
    def test_substack_hard_wall(self) -> None:
        html = '<div class="paywall">This post is for paid subscribers</div>'
        self.assertEqual(fx.detect_paywall(html, "short", "https://x.substack.com/p/y"), (True, "this post is for paid subscribers"))

    def test_metered_domains_stay_eligible(self) -> None:
        html = "You've hit the paywall. Subscribe to keep reading."
        self.assertEqual(fx.detect_paywall(html, "x" * 2000, "https://hbr.org/2026/08/a"), (False, "metered-ok"))

    def test_free_substack_with_subscribe_box(self) -> None:
        html = '<p>Subscribe now</p>' + "<p>body</p>" * 300
        self.assertEqual(fx.detect_paywall(html, "x" * 5000, "https://x.substack.com/p/free")[0], False)


class ReportFeedbackTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        good = _cand(1, topic="innovation", score=8.0, sender="fav@x.com")
        run = _cand(2, topic="innovation", score=5.0, sender="meh@x.com")
        sel = rk.select([good, run], RULES, per_topic=1)
        md = rp.render_report(title="t", window_start="2026-08-08", window_end="2026-08-15", edition_hint="N230", sel=sel,
                              emails=[{"message_id": "m1", "sender_name": "fav", "subject": "s", "timestamp": "2026-08-10",
                                       "sender_basis": "override:always", "is_new": False}],
                              cands_by_email={"m1": [good, run]}, new_senders=[("New Guy", "new@x.com", 2)],
                              floor_senders=[("Alumni", 3)], stats={"emails": 1, "links": 2, "scored": 2, "fetched": 0, "llm_calls": 0})
        self.assertIn("- [x]", md)
        self.assertIn("<!-- cand:m1:0 sender:fav@x.com -->", md)
        self.assertIn("New senders", md)
        decisions = fb.parse_report(md)
        self.assertEqual([(d["cid"], d["yes"]) for d in decisions], [("m1:0", True), ("m2:0", False)])
        # owner unticks the pick, ticks the runner-up
        md2 = md.replace("- [x] ", "- [ ] ", 1).replace("- [ ] **5.0**", "- [x] **5.0**")
        decisions = fb.parse_report(md2)
        self.assertEqual([d["yes"] for d in decisions], [False, True])
        with tempfile.TemporaryDirectory() as td:
            ov = Path(td) / "overrides.json"
            ov.write_text(json.dumps({"senders": {"pay@wall.com": {"tier": "never"}}}), encoding="utf-8")
            log = Path(td) / "feedback.jsonl"
            for k in range(3):   # three reports of yes for meh@x.com → usually
                res = fb.apply([{"cid": f"r{k}:0", "sender": "meh@x.com", "yes": True, "title": "t", "url": "u"}],
                               report_name=f"r{k}.md", overrides_path=ov, log_path=log)
            data = json.loads(ov.read_text(encoding="utf-8"))
            self.assertEqual(data["senders"]["meh@x.com"]["tier"], "usually")
            self.assertEqual(data["senders"]["pay@wall.com"]["tier"], "never")
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 3)
            # re-ingesting the same report replaces, never double-counts
            fb.apply([{"cid": "r0:0", "sender": "meh@x.com", "yes": False, "title": "t", "url": "u"}],
                     report_name="r0.md", overrides_path=ov, log_path=log)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 3)

    def test_tier_rules(self) -> None:
        self.assertIsNone(fb.tier_for(2, 2))
        self.assertEqual(fb.tier_for(3, 3), "usually")
        self.assertEqual(fb.tier_for(6, 6), "always")
        self.assertEqual(fb.tier_for(5, 0), "rarely")
        self.assertIsNone(fb.tier_for(4, 1))


if __name__ == "__main__":
    unittest.main()
