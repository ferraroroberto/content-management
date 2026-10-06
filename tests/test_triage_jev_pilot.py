"""Jev pilot measures (#285): owner/model agreement, calibration buckets, reliability, consistency, and the
`--shadow` compare reading a run's ``<report>.jev-shadow.json``. No network: the store is a fake client.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_triage_jev_pilot -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.topics import TOPICS  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage import jev_pilot as jp  # noqa: E402

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

    def __init__(self, report: Path) -> None:
        self.report = report

    def get_run(self, rid: int) -> Dict[str, Any]:
        return {"id": rid, "window_start": "2026-09-26", "window_end": "2026-10-03", "model": "claude_haiku",
                "report_path": str(self.report)}


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


if __name__ == "__main__":
    unittest.main()
