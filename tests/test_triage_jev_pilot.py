"""Jev pilot measures (#285): owner/model agreement, calibration buckets, reliability, consistency, and the
`--shadow` compare reading a run's ``<report>.jev-shadow.json``. No network: the store is a fake client.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_triage_jev_pilot -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.topics import TOPICS  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage import jev_pilot as jp  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402

L, P, I = TOPICS


def _item(cid: str, *, legacy: tuple, jev: Optional[tuple], pick: bool = False, decided: bool = False,
          owner_topic: Optional[str] = None, conf: float = 0.95) -> Dict[str, Any]:
    lt, lf = legacy
    j: Dict[str, Any] = {"status": jx.NOT_EVALUATED, "error": "http 504"}
    if jev is not None:
        jt, jf = jev
        j = {"status": "ok", "error": None, "topic": jt, "fit": jf, "news": False, "promo": False,
             "topic_conf": conf, "fit_conf": conf}
    return {"cid": cid, "title": cid, "lang": "en",
            "legacy": {"ok": True, "topic": lt, "fit": lf, "news": False, "promo": False}, "jev": j,
            "owner": {"decided": decided or pick, "pick": pick, "topic": owner_topic if pick else None}}


class MeasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            _item("a", legacy=(L, 5), jev=(L, 4), pick=True, owner_topic=L, conf=0.95),
            _item("b", legacy=(P, 4), jev=(I, 2), pick=True, owner_topic=P, conf=0.4),
            _item("c", legacy=(I, 4), jev=(I, 5), decided=True, conf=0.8),
            _item("d", legacy=(L, 1), jev=(L, 0), conf=0.6),
            _item("e", legacy=(P, 2), jev=None),
        ]
        self.m = jp.measures(self.items)

    def test_owner_agreement(self) -> None:
        leg, jev = self.m["owner_agreement"]["legacy"], self.m["owner_agreement"]["jev"]
        self.assertEqual((leg["recall_fit_ge3"], jev["recall_fit_ge3"]), (1.0, 0.5))
        self.assertEqual(leg["precision_fit_ge4"], round(2 / 3, 3))      # a, b picked; c not
        self.assertEqual(jev["precision_fit_ge4"], 0.5)                  # a picked; c not
        self.assertEqual(jev["topic_agreement_on_picks"], 0.5)

    def test_model_agreement_counts_both_evaluated_only(self) -> None:
        ma = self.m["model_agreement"]
        self.assertEqual(ma["n"], 4)
        self.assertEqual(ma["topic_match"], 0.75)
        self.assertEqual(ma["fit_within_1"], 0.75)
        self.assertEqual(ma["disagreements"], 1)

    def test_not_evaluated_is_its_own_state_not_a_disagreement(self) -> None:
        self.assertEqual(self.m["reliability"], {"legacy_unusable": 0.0, "jev_not_evaluated": 0.2,
                                                 "jev_errors": {"http 504": 1}})
        self.assertNotIn("e", [d["cid"] for d in self.m["_disagreements"]])

    def test_calibration_buckets_and_auto_accept(self) -> None:
        cal = self.m["calibration"]["topic_vs_owner_on_picks"]
        self.assertEqual(cal["buckets"]["≥0.9"], {"n": 1, "agreement": 1.0})
        self.assertEqual(cal["buckets"]["<0.5"], {"n": 1, "agreement": 0.0})
        self.assertEqual(cal["auto_accept@0.9"], {"share": 0.5, "agreement": 1.0})
        fit = self.m["calibration"]["fit_vs_owner_on_decided"]             # a, b, c decided
        self.assertEqual(fit["n"], 3)
        self.assertEqual(fit["buckets"]["0.7–0.9"], {"n": 1, "agreement": 0.0})   # c: fit 5, not picked

    def test_consistency(self) -> None:
        a = [{"status": "ok", "topic": L, "fit": 3, "news": False, "promo": False}] * 4
        b = a[:3] + [{"status": "ok", "topic": P, "fit": 4, "news": False, "promo": True}]
        c = jp.consistency(a, b)
        self.assertEqual((c["n"], c["topic_changed"], c["fit_band_changed"], c["promo_changed"]), (4, 0.25, 0.25, 0.25))

    def test_lang(self) -> None:
        self.assertEqual(jp.lang("How to lead a team through change"), "en")
        self.assertEqual(jp.lang("Cómo liderar un equipo en tiempos de cambio"), "other")
        self.assertEqual(jp.lang("GPT-5"), "unknown")


class _Store:
    """Just enough of the store for ``stage_a_items`` + ``shadow``."""

    def __init__(self, report: Path, stats: Optional[Dict[str, Any]] = None) -> None:
        self.report = report
        self.stats = stats or {}

    def get_run(self, rid: int) -> Dict[str, Any]:
        return {"id": rid, "window_start": "2026-09-26", "window_end": "2026-10-03", "model": "claude_haiku",
                "report_path": str(self.report), "stats": dict(self.stats)}


class ShadowCompareTests(unittest.TestCase):
    def test_reads_the_shadow_file_beside_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "triage-2026-09-26_2026-10-03.md"
            rows = [{"cid": "m1:0", "status": "ok", "error": None, "topic": L, "fit": 4, "news": False, "promo": False,
                     "topic_conf": 0.97, "fit_conf": 0.8, "probs": {}}]
            jx.write_shadow(jx.shadow_path(report), rows, window=("2026-09-26", "2026-10-03"))
            store = _Store(report)
            cands = [{"cid": "m1:0", "message_id": "m1", "canonical": "u1", "url": "https://x.com/a", "title": "A",
                      "domain": "x.com", "meta": {"ok": True, "topic": L, "fit": 5, "news": False, "promo": False}},
                     {"cid": "m2:0", "message_id": "m2", "canonical": "u2", "url": "https://y.com/b", "title": "B",
                      "domain": "y.com", "meta": {"ok": True, "topic": P, "fit": 2, "news": False, "promo": False}},
                     {"cid": "m3:0", "message_id": "m3", "canonical": "u3", "url": "https://z.com/c", "title": "C",
                      "domain": "z.com", "meta": None}]                    # not a Stage A item
            patches = {"get_run": store.get_run, "get_review": lambda *_a: {"comment": ""},
                       "emails": lambda _r: [{"message_id": "m1", "sender_name": "S1"}],
                       "candidates": lambda _r: cands,
                       "load_decisions": lambda *_a: [{"canonical": "u1", "pick": True, "topic": L}]}
            saved = {k: getattr(db, k) for k in patches}
            try:
                for k, v in patches.items():
                    setattr(db, k, v)
                result = jp.shadow([17])
            finally:
                for k, v in saved.items():
                    setattr(db, k, v)
        p = result["pooled"]
        self.assertEqual(p["links"], 2)
        self.assertEqual(p["reliability"]["jev_errors"], {"not in shadow file": 1})
        self.assertEqual(p["owner_agreement"]["jev"]["recall_fit_ge3"], 1.0)
        self.assertEqual(result["runs"]["17"]["_items"][0]["input"],
                         {"sender": "S1", "label": "A", "domain": "x.com", "path": "/a"})
        self.assertEqual(result["runs"]["17"]["decided_by"], "legacy")

    def test_jev_decided_run_reads_legacy_from_its_shadow_file(self) -> None:  # `jev+legacy`, #394
        def jev_meta(topic: str, fit: int) -> Dict[str, Any]:
            m = jx.JevMetaScore(topic=topic, fit=fit, fit_score=float(fit), news_p=0.1, promo_p=0.1, ok=True,
                                conf={"topic": 0.95, "fit": 0.6, "news": 0.8, "promo": 0.8})
            return dict(m.__dict__)

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "triage-2026-09-26_2026-10-03.md"
            jx.write_shadow(jx.legacy_shadow_path(report), sc.legacy_shadow_rows(
                ["m1:0"], [sc.MetaScore(topic=P, fit=1, reason="weak", ok=True)], model="claude_haiku"),
                window=("2026-09-26", "2026-10-03"), engine="legacy")
            store = _Store(report, stats={"stage_a_engine": "jev+legacy", "legacy_shadow": "x"})
            cands = [{"cid": "m1:0", "message_id": "m1", "canonical": "u1", "url": "https://x.com/a", "title": "A",
                      "domain": "x.com", "meta": jev_meta(L, 4)},
                     {"cid": "m2:0", "message_id": "m2", "canonical": "u2", "url": "https://y.com/b", "title": "B",
                      "domain": "y.com", "meta": jev_meta(I, 2)}]
            patches = {"get_run": store.get_run, "get_review": lambda *_a: {"comment": ""},
                       "emails": lambda _r: [{"message_id": "m1", "sender_name": "S1"}],
                       "candidates": lambda _r: cands,
                       "load_decisions": lambda *_a: [{"canonical": "u1", "pick": True, "topic": L}]}
            with mock.patch.multiple(db, **patches):
                result = jp.shadow([18])
        run = result["runs"]["18"]
        self.assertEqual(run["decided_by"], "jev")
        first, second = run["_items"]
        self.assertEqual((first["jev"]["topic"], first["jev"]["fit"], first["jev"]["topic_conf"]), (L, 4, 0.95))
        self.assertEqual((first["legacy"]["ok"], first["legacy"]["topic"], first["legacy"]["fit"]), (True, P, 1))
        self.assertFalse(second["legacy"]["ok"])                          # not in the legacy shadow file
        oa = result["pooled"]["owner_agreement"]
        self.assertEqual((oa["jev"]["recall_fit_ge3"], oa["legacy"]["recall_fit_ge3"]), (1.0, 0.0))

    def test_jev_decided_run_without_shadow_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _Store(Path(tmp) / "triage-2026-09-26_2026-10-03.md", stats={"stage_a_engine": "jev"})
            with mock.patch.multiple(db, get_run=store.get_run, get_review=lambda *_a: {"comment": ""},
                                     emails=lambda _r: [], candidates=lambda _r: [], load_decisions=lambda *_a: []):
                result = jp.shadow([19])
        self.assertEqual((result["runs"], result["pooled"]), ({}, None))


class CallSinkTests(unittest.TestCase):
    """Both scorers report one record per hub call: timing + the response's token usage (#392)."""

    def test_legacy_sink_records_each_batch_with_usage(self) -> None:
        usage = {"input_tokens": 900, "output_tokens": 120, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 4000}
        reply = '[{"i": 1, "topic": "innovation", "fit": 3, "news": false, "promo": false, "reason": "x"}]'
        items = [{"sender": "S", "label": f"link {i}", "domain": "x.com", "path": f"/{i}"} for i in range(3)]
        calls: List[Dict[str, Any]] = []
        with mock.patch.object(sc.llm, "call_with_usage", return_value=(reply, usage)) as call:
            out = sc.score_metadata(items, {"rules": {}}, base_url="http://hub", model="m", batch=2, calls=calls)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(sorted(c["links"] for c in calls), [1, 2])
        self.assertTrue(all(c["ok"] and c["usage"] == usage and c["end"] >= c["start"] for c in calls))
        self.assertTrue(out[0].ok)

    def test_meta_request_is_what_the_scorer_sends(self) -> None:
        items = [{"sender": "S", "label": "A title", "domain": "x.com", "path": "/a"}]
        prompt, max_tokens = sc.meta_request("BRIEF", items)
        with mock.patch.object(sc.llm, "call_with_usage", return_value=("[]", {})) as call:
            sc.score_metadata(items, {"rules": {}}, base_url="http://hub", model="m", calls=[])
        self.assertEqual(call.call_args.kwargs["max_tokens"], max_tokens)
        self.assertTrue(prompt.endswith('1. sender="S" | anchor="A title" | domain=x.com | path=/a'))
        self.assertTrue(call.call_args.kwargs["prompt"].endswith(prompt[len("BRIEF"):]))

    def test_jev_sink_records_each_link_with_usage(self) -> None:
        answer = {"model": "jev-1.13.0", "usage": {"input_tokens": 1700, "output_tokens": 90}, "answers": {
            "topic": {"choice": "innovation", "confidence": 0.9, "probabilities": {}},
            "fit": {"score": 2.2, "confidence": 0.5, "probabilities": {}},
            "news": {"noul": 0.1}, "promo": {"noul": 0.2}}}
        resp = mock.Mock(status_code=200, headers={})
        resp.json.return_value = answer
        session = mock.Mock()
        session.post.return_value = resp
        calls: List[Dict[str, Any]] = []
        with mock.patch.object(jx.requests, "Session", return_value=session):
            out = jx.score_metadata_jev([{"sender": "S", "label": "A", "domain": "x.com", "path": "/a"}] * 2,
                                        {"rules": {}}, base_url="http://hub", calls=calls)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["usage"], {"input_tokens": 1700, "output_tokens": 90})
        self.assertEqual(out[0].usage, calls[0]["usage"])


class PerfTests(unittest.TestCase):
    def test_concurrency_reached_and_tokens(self) -> None:
        calls = [{"links": 25, "start": 0.0, "end": 10.0, "ok": True,
                  "usage": {"input_tokens": 10, "output_tokens": 500, "cache_creation_input_tokens": 4000}},
                 {"links": 25, "start": 2.0, "end": 9.0, "ok": True,
                  "usage": {"input_tokens": 10, "output_tokens": 500, "cache_read_input_tokens": 3000}},
                 {"links": 10, "start": 10.0, "end": 14.0, "ok": False, "usage": {}}]   # starts as the first ends
        p = jp.perf(calls, wall_s=14.0, n_links=60)
        self.assertEqual(p["max_in_flight"], 2)
        self.assertEqual((p["calls"], p["failed_calls"], p["calls_without_usage"]), (3, 1, 1))
        self.assertEqual(p["input_tokens"], 7020)
        self.assertEqual((p["cache_write_tokens"], p["cache_read_tokens"], p["output_tokens"]), (4000, 3000, 1000))
        self.assertEqual(p["ms_per_link"], round(14.0 / 60 * 1000, 1))

    def test_no_calls(self) -> None:
        self.assertEqual(jp.perf([], wall_s=0.0, n_links=0), {"calls": 0, "links": 0, "wall_s": 0.0})


class StageBEntryTests(unittest.TestCase):
    def test_ranks_like_run_py_and_counts_picks_reached(self) -> None:
        def it(cid: str, fit: int, *, fetched: bool = False, pick: bool = False, news: bool = False) -> Dict[str, Any]:
            return {"cid": cid, "domain": "x.com", "sender_weight": 1.0, "fetched": fetched,
                    "legacy": {"ok": True, "topic": L, "fit": fit, "news": news, "promo": False},
                    "jev": {"status": "ok", "topic": L, "fit": 5 - fit, "news": False, "promo": False},
                    "owner": {"decided": pick, "pick": pick, "topic": L if pick else None}}
        items = [it("a", 5, fetched=True, pick=True), it("b", 4, fetched=True), it("c", 5, news=True),
                 it("d", 1, pick=True)]
        priors = mock.Mock()
        priors.domain.return_value = (0.0, 0)
        leg = jp.stage_b_entry(items, "legacy", priors, depth=2)          # a (5) and b (4) beat c (5, news × 0.6)
        self.assertEqual((leg["overlap_with_original"], leg["owner_picks_reached"]), (1.0, 1))
        jev = jp.stage_b_entry(items, "jev", priors, depth=2)             # d (4) and b (1) beat a (0) and c (0)
        self.assertEqual((jev["overlap_with_original"], jev["owner_picks_reached"]), (0.5, 1))
        self.assertEqual(jev["original_fetched"], 2)


class LegacyReplayTests(unittest.TestCase):
    def test_both_engines_interleave_and_reproduction(self) -> None:
        cands = [{"cid": f"m{i}:0", "message_id": f"m{i}", "canonical": f"u{i}", "url": f"https://x.com/{i}",
                  "title": f"T{i}", "domain": "x.com", "sender_weight": 1.0, "fetched_ok": i == 0,
                  "meta": {"ok": True, "topic": L, "fit": 4, "news": False, "promo": False}} for i in range(2)]
        patches = {"get_run": lambda rid: {"id": rid, "window_start": "2026-09-26", "window_end": "2026-10-03",
                                           "model": "claude_haiku", "report_path": None},
                   "get_review": lambda *_a: None, "emails": lambda _r: [], "candidates": lambda _r: cands,
                   "load_decisions": lambda *_a: []}
        order: List[str] = []

        def legacy(items: Any, _crit: Any, **kw: Any) -> List[sc.MetaScore]:
            order.append("legacy")
            kw["calls"].append({"links": len(items), "start": 0.0, "end": 1.0, "ok": True, "usage": {"input_tokens": 5}})
            return [sc.MetaScore(topic=L, fit=4, ok=True), sc.MetaScore(topic=P, fit=1, ok=True)]

        def jev(items: Any, _crit: Any, **_kw: Any) -> List[jx.JevMetaScore]:
            order.append("jev")
            return [jx.JevMetaScore(topic=L, fit=3, ok=True, conf={"topic": 0.9, "fit": 0.5}) for _ in items]

        legacy_cfg = {"model": "m", "workers": 4, "top_k": 1}
        priors = mock.Mock()
        priors.domain.return_value = (0.0, 0)
        saved = {k: getattr(db, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(db, k, v)
            with mock.patch.object(sc, "score_metadata", legacy), mock.patch.object(jx, "score_metadata_jev", jev), \
                    mock.patch.object(jp, "RingRecorder"), mock.patch.object(sc, "Priors", return_value=priors):
                with self.assertRaises(SystemExit):                       # unreviewed is refused by default
                    jp.replay([17], base_url="http://hub", model="jev-latest", workers=2, repeat=False,
                              criteria={}, legacy=legacy_cfg)
                result = jp.replay([17, 16], base_url="http://hub", model="jev-latest", workers=2, repeat=False,
                                   criteria={}, legacy=legacy_cfg, require_review=False)
        finally:
            for k, v in saved.items():
                setattr(db, k, v)
        self.assertEqual(order, ["legacy", "jev", "jev", "legacy"])      # the first engine alternates per run
        r = result["runs"]["17"]
        self.assertEqual(r["engines"]["legacy"]["calls"], 1)
        self.assertEqual(r["engines"]["jev"]["calls"], 0)                 # the patched scorer reported none
        rep = r["reproduction"]["legacy_fresh_vs_stored"]
        self.assertEqual((rep["n"], rep["topic_changed"], rep["fit_band_changed"]), (2, 0.5, 0.5))
        self.assertEqual(result["mode"], "replay + legacy")
        self.assertIn("pooled_fresh_legacy", result)


class PlainReplayTests(unittest.TestCase):
    def test_replay_without_legacy_writes_its_artefact(self) -> None:
        cands = [{"cid": f"m{i}:0", "message_id": f"m{i}", "canonical": f"u{i}", "url": f"https://x.com/{i}",
                  "title": f"T{i}", "domain": "x.com", "sender_weight": 1.0, "fetched_ok": True,
                  "meta": {"ok": True, "topic": L, "fit": 4, "news": False, "promo": False}} for i in range(2)]
        patches = {"get_run": lambda rid: {"id": rid, "window_start": "2026-09-26", "window_end": "2026-10-03",
                                           "model": "claude_haiku", "report_path": None},
                   "get_review": lambda *_a: None, "emails": lambda _r: [], "candidates": lambda _r: cands,
                   "load_decisions": lambda *_a: []}

        def jev(items: Any, _crit: Any, **_kw: Any) -> List[jx.JevMetaScore]:
            return [jx.JevMetaScore(topic=L, fit=3, ok=True, conf={"topic": 0.9, "fit": 0.5}) for _ in items]

        saved = {k: getattr(db, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(db, k, v)
            with mock.patch.object(jx, "score_metadata_jev", jev), mock.patch.object(jp, "RingRecorder"):
                result = jp.replay([17], base_url="http://hub", model="jev-latest", workers=2, repeat=False,
                                   criteria={}, require_review=False)       # legacy=None: the plain `--run 17`
        finally:
            for k, v in saved.items():
                setattr(db, k, v)
        self.assertEqual(result["mode"], "replay")
        self.assertNotIn("engines", result["runs"]["17"])
        with tempfile.TemporaryDirectory() as tmp:
            result["generated_at"] = "2026-10-17T00:00:00Z"
            out = jp.write_artefact(result, Path(tmp))
            self.assertTrue((out / "summary.json").is_file())
            self.assertTrue((out / "items-run-17.json").is_file())


if __name__ == "__main__":
    unittest.main()
