"""Stage A engine switch + the TypeSafe Jev scorer (#285). No network: the hub is faked at the
``requests`` boundary, the legacy scorer and the store are patched.

Covers: ``legacy`` runs exactly the pre-#285 path (same scorer call, no Jev, no new stats, no shadow),
``both`` keeps legacy's verdicts byte-for-byte even when Jev fails or hangs past its budget, Jev
answers parse into typed scores with their confidences, failures are ``not evaluated`` (never 0),
only link metadata leaves the machine, and nothing in the repo calls the vendor URL directly.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_triage_jev -v
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from newsletter.topics import TOPICS  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage import rank as rk  # noqa: E402
from newsletter.triage import run  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402

CRITERIA = {"rules": {
    "topics": {TOPICS[0]: {"themes": ["feedback", "teams"], "anti_themes": ["hiring ads"]},
               TOPICS[1]: {"themes": ["habits"], "anti_themes": ["self-help listicles"]},
               TOPICS[2]: {"themes": ["AI at work"], "anti_themes": ["release notes"]}},
    "news_policy": {"rule": "commentary over releases", "drop_if": ["funding rounds"]},
    "content_exclusions": {"anchors": ["webinar", "sponsor"]},
    "caps": {}, "edition": {}}}

ANSWER = {"model": "jev-1.13.0", "usage": {"input_tokens": 500, "output_tokens": 90}, "answers": {
    "topic": {"type": "choice", "choice": "personal_development", "confidence": 0.82,
              "probabilities": {"leadership": 0.1, "personal_development": 0.88, "innovation": 0.02}},
    "fit": {"type": "score", "score": 3.6, "confidence": 0.71,
            "probabilities": {"0": 0.0, "1": 0.02, "2": 0.08, "3": 0.25, "4": 0.6, "5": 0.05}},
    "news": {"type": "noul", "noul": 0.1},
    "promo": {"type": "noul", "noul": 0.7}}}

ITEM = {"sender": "Ness Labs", "label": "The science of deep focus", "domain": "nesslabs.com", "path": "/focus"}


class _Resp:
    def __init__(self, status: int, body: Any = None, headers: Dict[str, str] = None) -> None:
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session:
    """Records every POST; answers from a list of responses (the last one repeats)."""

    def __init__(self, *responses: Any) -> None:
        self.responses, self.calls = list(responses), []

    def post(self, url: str, json: Any = None, timeout: Any = None) -> _Resp:
        self.calls.append({"url": url, "json": json})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


class QuestionAndAnswerTests(unittest.TestCase):
    def test_state_carries_link_metadata_only(self) -> None:
        state = jx.build_state(dict(ITEM, sender_address="someone@example.com", body="full email text"))
        self.assertEqual(set(state), {"link"})
        self.assertEqual(set(state["link"]), {"sender", "anchor_text", "domain", "path"})
        self.assertNotIn("someone@example.com", str(state))

    def test_questions_are_typed_and_trimmed_per_question(self) -> None:
        q = jx.build_questions(CRITERIA, lessons=["prefer first-hand essays"])
        self.assertEqual({k: v["type"] for k, v in q.items()},
                         {"topic": "choice", "fit": "score", "news": "noul", "promo": "noul"})
        self.assertEqual(set(q["topic"]["criteria"]), set(jx.TOPIC_KEYS))
        self.assertEqual(len(q["fit"]["criteria"]), 6)                    # the 0–5 rubric levels
        self.assertIn("owner_preferences", q["fit"]["instructions"])     # lessons go to fit only
        self.assertNotIn("prefer first-hand essays", str(q["news"]) + str(q["promo"]) + str(q["topic"]))

    def test_parse_full_answer(self) -> None:
        m = jx.parse_answers(ANSWER)
        self.assertTrue(m.ok)
        self.assertEqual((m.topic, m.fit, m.news, m.promo), (TOPICS[1], 4, False, True))
        self.assertEqual(m.fit_score, 3.6)
        self.assertEqual(m.conf["topic"], 0.82)
        self.assertEqual(m.conf["fit"], 0.71)
        self.assertAlmostEqual(m.conf["news"], 0.8)
        self.assertAlmostEqual(m.conf["promo"], 0.4)
        self.assertEqual(m.reason, "")                                   # Jev produces no text
        self.assertEqual(m.served_model, "jev-1.13.0")

    def test_incomplete_answer_is_not_evaluated_not_zero(self) -> None:
        doc = {"model": "jev-1.13.0", "answers": {k: v for k, v in ANSWER["answers"].items() if k != "fit"}}
        m = jx.parse_answers(doc)
        self.assertFalse(m.ok)
        self.assertIsNone(m.fit)
        self.assertEqual(m.error, "incomplete answers")


class TransportTests(unittest.TestCase):
    def test_calls_the_hub_route_only(self) -> None:
        s = _Session(_Resp(200, ANSWER))
        m = jx.evaluate(ITEM, jx.build_questions(CRITERIA, lessons=[]), base_url="http://127.0.0.1:8000/", session=s)
        self.assertTrue(m.ok)
        self.assertEqual(s.calls[0]["url"], "http://127.0.0.1:8000/v1/systemone")
        self.assertEqual(s.calls[0]["json"]["model"], jx.DEFAULT_MODEL)

    def test_hub_error_is_its_own_state(self) -> None:
        s = _Session(_Resp(503, {"detail": "x", "hub_error": "typesafe_not_configured"}))
        m = jx.evaluate(ITEM, {}, base_url="http://hub", session=s)
        self.assertFalse(m.ok)
        self.assertEqual(m.error, "http 503 typesafe_not_configured")

    def test_unreachable_hub_never_raises(self) -> None:
        s = _Session(requests.ConnectionError("refused"))
        m = jx.evaluate(ITEM, {}, base_url="http://hub", session=s)
        self.assertEqual((m.ok, m.error), (False, "hub unreachable (ConnectionError)"))

    def test_429_is_retried_once(self) -> None:
        s = _Session(_Resp(429, {}, {"Retry-After": "1"}), _Resp(200, ANSWER))
        with mock.patch.object(jx.time, "sleep") as slept:
            m = jx.evaluate(ITEM, {}, base_url="http://hub", session=s)
        self.assertTrue(m.ok)
        self.assertEqual(len(s.calls), 2)
        slept.assert_called_once_with(1.0)

    def test_no_direct_vendor_calls_in_the_repo(self) -> None:
        hits = [str(p.relative_to(REPO_ROOT)) for d in ("newsletter", "app", "config")
                for p in (REPO_ROOT / d).rglob("*.py") if "api.typesafe.ai" in p.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])


class ShadowTests(unittest.TestCase):
    def test_budget_bounds_a_hanging_jev(self) -> None:
        release = threading.Event()

        def slow(*_a: Any, **_k: Any) -> jx.JevMetaScore:
            release.wait(5)
            return jx.parse_answers(ANSWER)

        with mock.patch.object(jx, "evaluate", side_effect=slow):
            t0 = time.monotonic()
            shadow = jx.Shadow([ITEM] * 3, CRITERIA, base_url="http://hub", workers=1, budget_s=0.2)
            out = shadow.collect()
            elapsed = time.monotonic() - t0
            release.set()
        self.assertLess(elapsed, 2.0)
        self.assertTrue(all(not m.ok and m.error == jx.NOT_EVALUATED for m in out))
        rows = jx.shadow_rows(["a", "b", "c"], out, model="jev-latest")
        self.assertEqual({r["status"] for r in rows}, {jx.NOT_EVALUATED})
        self.assertTrue(all(r["fit"] is None and r["news"] is None for r in rows))


# ---------------------------------------------------------------------------
# the engine switch inside one window


def _cands() -> List[rk.Candidate]:
    out = []
    for i in range(4):
        url = f"https://site{i}.com/post-{i}"
        out.append(rk.Candidate(cid=f"m{i}:0", message_id=f"m{i}", sender_name=f"S{i}", sender_address=f"s{i}@x.com",
                                subject="s", email_ts="2026-08-10T10:00:00+00:00", label=f"A long enough anchor {i}",
                                url=url, canonical=url, domain=f"site{i}.com", sender_weight=1.0))
    return out


def _legacy_scores(items: Any, *_a: Any, **_k: Any) -> List[sc.MetaScore]:
    return [sc.MetaScore(topic=TOPICS[i % 3], fit=5 - i, reason=f"r{i}", ok=True) for i in range(len(items))]


class EngineSwitchTests(unittest.TestCase):
    def _body(self, engine: str, **patches: Any) -> Any:
        cands = _cands()
        by_email = {c.message_id: [c] for c in cands}
        legacy = mock.MagicMock(side_effect=_legacy_scores)
        cache = mock.MagicMock(misses=0)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run, "emails_from_cache", return_value=[]), \
                mock.patch.object(run, "load_notion_urls", return_value=(set(), {})), \
                mock.patch.object(run, "store_ok", return_value=False), \
                mock.patch.object(run, "build_candidates", return_value=(cands, by_email)), \
                mock.patch.object(run.llm, "health_check", return_value=True), \
                mock.patch.object(run.sc, "score_metadata", legacy), \
                mock.patch.object(run.sc, "LLMCache", return_value=cache), \
                mock.patch.object(run.sc, "Priors", return_value=mock.MagicMock(ranked={})):
            for target, value in patches.items():
                mock.patch.object(jx, target, value).start()
            try:
                path, sel, stats, out, _emails = run._run_window_body(
                    date(2026, 8, 8), date(2026, 8, 15), cfg={"llm_hub_base_url": "http://hub", "jev_budget_s": 0.3},
                    criteria=CRITERIA, source="cache", use_llm=True, use_fetch=False, top_k=10, model="claude_haiku",
                    out_dir=Path(tmp), edition_hint="next", backtest=None, limit_links=None, engine=engine)
                shadow_doc = jx.read_shadow(jx.shadow_path(path))
            finally:
                mock.patch.stopall()
        shadow = shadow_doc["rows"] if shadow_doc else None
        stats.pop("elapsed_s")
        return legacy, stats, db.candidate_rows(out, sel), shadow

    def test_legacy_is_the_pre_285_path(self) -> None:
        with mock.patch.object(jx, "Shadow") as shadow_cls, mock.patch.object(jx, "score_metadata_jev") as jev_score:
            legacy, stats, rows, shadow = self._body("legacy")
        legacy.assert_called_once()
        args, kwargs = legacy.call_args
        self.assertEqual(kwargs, {"base_url": "http://hub", "model": "claude_haiku", "workers": 4, "cache": mock.ANY})
        shadow_cls.assert_not_called()
        jev_score.assert_not_called()
        self.assertIsNone(shadow)
        self.assertEqual(set(stats), {"emails", "links", "duplicates_in_notion", "duplicates_picked_earlier",
                                      "unresolved_links", "stage_a_scored", "scored", "llm_calls", "selected",
                                      "verdicts"})
        self.assertEqual(rows[0]["meta"], {"topic": TOPICS[0], "fit": 5, "news": False, "promo": False,
                                           "reason": "r0", "ok": True})

    def test_both_keeps_legacy_verdicts_when_jev_fails(self) -> None:
        _l, _s, legacy_rows, _ = self._body("legacy")
        failing = mock.MagicMock(return_value=jx.JevMetaScore(error="http 502 typesafe_unreachable"))
        _l, stats, both_rows, shadow = self._body("both", evaluate=failing)
        self.assertEqual(both_rows, legacy_rows)
        self.assertEqual(stats["stage_a_engine"], "both")
        self.assertEqual((stats["jev_evaluated"], stats["jev_not_evaluated"]), (0, 4))
        self.assertEqual([r["status"] for r in shadow], [jx.NOT_EVALUATED] * 4)
        self.assertEqual(shadow[0]["error"], "http 502 typesafe_unreachable")

    def test_both_stores_jev_beside_legacy(self) -> None:
        _l, _s, legacy_rows, _ = self._body("legacy")
        ok = mock.MagicMock(side_effect=lambda *a, **k: jx.parse_answers(ANSWER))
        _l, stats, both_rows, shadow = self._body("both", evaluate=ok)
        self.assertEqual(both_rows, legacy_rows)                          # Jev never decides in `both`
        self.assertEqual(stats["jev_evaluated"], 4)
        self.assertEqual([r["cid"] for r in shadow], [f"m{i}:0" for i in range(4)])
        self.assertEqual((shadow[0]["topic"], shadow[0]["fit"], shadow[0]["topic_conf"]), (TOPICS[1], 4, 0.82))

    def test_jev_engine_decides_and_keeps_confidence(self) -> None:
        ok = mock.MagicMock(side_effect=lambda *a, **k: jx.parse_answers(ANSWER))
        legacy, stats, rows, shadow = self._body("jev", evaluate=ok)
        legacy.assert_not_called()
        self.assertIsNone(shadow)
        self.assertEqual(stats["stage_a_engine"], "jev")
        self.assertEqual(rows[0]["meta"]["topic"], TOPICS[1])
        self.assertEqual(rows[0]["meta"]["conf"]["fit"], 0.71)
        self.assertEqual(rows[0]["verdict"], "vetoed")                    # promo_p 0.7 → promo veto


class ShadowFileTests(unittest.TestCase):
    def test_round_trip_beside_the_report(self) -> None:
        rows = jx.shadow_rows(["a", "b"], [jx.parse_answers(ANSWER), jx.JevMetaScore(error="http 504")],
                              model="jev-latest")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "triage-2026-08-08_2026-08-15.md"
            path = jx.shadow_path(report)
            self.assertEqual(path.name, "triage-2026-08-08_2026-08-15.jev-shadow.json")
            jx.write_shadow(path, rows, window=("2026-08-08", "2026-08-15"))
            doc = jx.read_shadow(path)
        self.assertEqual(doc["evaluated"], 1)
        self.assertEqual([(r["cid"], r["status"], r["error"]) for r in doc["rows"]],
                         [("a", "ok", None), ("b", jx.NOT_EVALUATED, "http 504")])

    def test_write_failure_never_fails_the_run(self) -> None:
        stats: Dict[str, Any] = {"jev_not_evaluated": 0}
        with mock.patch.object(jx, "write_shadow", side_effect=OSError("disk full")):
            run._write_shadow(Path("x.md"), [], start=date(2026, 8, 8), end=date(2026, 8, 15), stats=stats)
        self.assertEqual(stats["jev_shadow"], "failed: OSError")


if __name__ == "__main__":
    unittest.main()
