"""Triage store + review logic against an in-memory fake of the supabase-py table chain.

Covers: run registration / replace semantics, candidate row shaping, decisions keyed by window+canonical
(survive a re-run, pre-fill the review frame), the tier tally, history import from a fixture, lessons
accept → lessons.json export, Saturday windows, manual sender tiers. No network.

An optional integration test (``TRIAGE_DB_INTEGRATION=1`` + ``config.supabase``) probes the real schema.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import db  # noqa: E402
from newsletter.triage import feedback as fb  # noqa: E402
from newsletter.triage import lessons as ls  # noqa: E402
from newsletter.triage import rank as rk  # noqa: E402
from newsletter.triage import review as rv  # noqa: E402
from newsletter.triage.score import MetaScore  # noqa: E402


# ---------------------------------------------------------------------------
# fake supabase-py


class _Result:
    def __init__(self, data: List[Dict[str, Any]], count: Optional[int] = None) -> None:
        self.data, self.count = data, count


class _Query:
    def __init__(self, store: "FakeSupabase", name: str) -> None:
        self.store, self.name = store, name
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters: List[tuple] = []
        self.orders: List[tuple] = []
        self._range: Optional[tuple] = None
        self._limit: Optional[int] = None
        self._count = None

    # builders
    def select(self, cols: str = "*", count: Optional[str] = None) -> "_Query":
        self.op, self.cols, self._count = "select", cols, count
        return self

    def insert(self, rows: Any) -> "_Query":
        self.op, self.payload = "insert", rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows: Any, on_conflict: str = "") -> "_Query":
        self.op, self.payload, self.on_conflict = "upsert", list(rows), on_conflict
        return self

    def update(self, patch: Dict[str, Any]) -> "_Query":
        self.op, self.payload = "update", patch
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: List[Any]) -> "_Query":
        self.filters.append(("in", col, list(vals)))
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        self.orders.append((col, desc))
        return self

    def range(self, a: int, b: int) -> "_Query":
        self._range = (a, b)
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = n
        return self

    # execution
    def _match(self, row: Dict[str, Any]) -> bool:
        for kind, col, val in self.filters:
            if kind == "eq" and str(row.get(col)) != str(val):
                return False
            if kind == "in" and row.get(col) not in val:
                return False
        return True

    def execute(self) -> _Result:
        table = self.store.tables.setdefault(self.name, [])
        if self.op == "select":
            rows = [dict(r) for r in table if self._match(r)]
            for col, desc in reversed(self.orders):
                rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col))), reverse=desc)
            total = len(rows)
            if self._range:
                rows = rows[self._range[0]: self._range[1] + 1]
            if self._limit is not None:
                rows = rows[: self._limit]
            return _Result(rows, total if self._count else None)
        if self.op == "insert":
            out = []
            for r in self.payload:
                r = dict(r)
                if self.name in self.store.serial:
                    self.store.serial[self.name] += 1
                    r.setdefault("id", self.store.serial[self.name])
                table.append(r)
                out.append(dict(r))
            return _Result(out)
        if self.op == "upsert":
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            out = []
            for r in self.payload:
                r = dict(r)
                hit = next((t for t in table if keys and all(str(t.get(k)) == str(r.get(k)) for k in keys)), None)
                if hit is not None:
                    hit.update(r)
                else:
                    table.append(r)
                out.append(dict(r))
            return _Result(out)
        if self.op == "update":
            out = []
            for r in table:
                if self._match(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return _Result(out)
        if self.op == "delete":
            victims = [r for r in table if self._match(r)]
            self.store.tables[self.name] = [r for r in table if not self._match(r)]
            if self.name == "triage_runs":   # emulate `on delete cascade`
                ids = {v["id"] for v in victims}
                for child in ("triage_emails", "triage_candidates"):
                    self.store.tables[child] = [r for r in self.store.tables.get(child, []) if r.get("run_id") not in ids]
                for r in self.store.tables.get("triage_lessons", []):
                    if r.get("run_id") in ids:
                        r["run_id"] = None
            return _Result(victims)
        raise AssertionError(self.op)


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.serial = {"triage_runs": 0, "triage_lessons": 0}

    def table(self, name: str) -> _Query:
        return _Query(self, name)


# ---------------------------------------------------------------------------
# helpers


def _cand(i: int, *, topic: str, score: float, sender: str = "a@x.com", url: Optional[str] = None) -> rk.Candidate:
    url = url or f"https://site{i}.com/post-{i}"
    c = rk.Candidate(cid=f"m{i}:0", message_id=f"m{i}", sender_name=f"S{i}", sender_address=sender, subject="s",
                     email_ts="2026-08-10T10:00:00+00:00", label=f"Title {i}", url=url, canonical=url,
                     domain=f"site{i}.com", title=f"Title {i}", score=score)
    c.meta = MetaScore(topic=topic, fit=4, reason="fits", ok=True)
    c.topic = topic
    return c


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeSupabase()
        db.set_client(self.fake)

    def tearDown(self) -> None:
        db.set_client(None)

    def _store(self, start: str = "2026-08-08", end: str = "2026-08-15", *, scores=(9.0, 8.5, 5.0)) -> int:
        cands = [_cand(1, topic="innovation", score=scores[0], sender="fav@x.com"),
                 _cand(2, topic="innovation", score=scores[1], sender="meh@x.com"),
                 _cand(3, topic="innovation", score=scores[2], sender="meh@x.com")]
        sel = rk.select(cands, {}, per_topic=2, runners_n=2)
        run_id = db.register_run(date.fromisoformat(start), date.fromisoformat(end), source="cache", model="m", criteria_version="1")
        emails = [{"message_id": f"m{i}", "sender_name": f"S{i}", "sender_address": c.sender_address, "subject": "s",
                   "timestamp": c.email_ts, "sender_basis": "ranked", "is_new": i == 3} for i, c in enumerate(cands, 1)]
        db.store_run_results(run_id, emails, db.candidate_rows(cands, sel))
        db.mark_run(run_id, status="done", stats={"selected": 2}, report_path="r.md")
        return run_id

    def test_register_replace_and_rows(self) -> None:
        r1 = self._store()
        self.assertEqual(db.get_run(r1)["status"], "done")
        rows = db.candidates(r1)
        self.assertEqual(len(rows), 3)
        picks = sorted(r["cid"] for r in rows if r["suggested"] == "pick")
        self.assertEqual(picks, ["m1:0", "m2:0"])
        self.assertEqual(next(r for r in rows if r["cid"] == "m1:0")["suggested_rank"], 1)
        self.assertTrue(next(r for r in rows if r["cid"] == "m1:0")["suggested_star"])
        self.assertEqual(rows[0]["meta"]["topic"], "innovation")
        self.assertEqual(len(db.emails(r1)), 3)
        # same window again → replaced, children gone with it
        r2 = self._store()
        self.assertNotEqual(r1, r2)
        self.assertIsNone(db.get_run(r1))
        self.assertEqual({r["run_id"] for r in self.fake.tables["triage_candidates"]}, {r2})
        runs = db.list_runs()
        self.assertEqual([r["id"] for r in runs], [r2])
        self.assertFalse(runs[0]["reviewed"])
        self.assertEqual(runs[0]["picks"], 2)

    def test_review_frame_apply_and_rerun_keeps_ticks(self) -> None:
        run_id = self._store()
        frame = rv.review_frame(run_id)
        self.assertEqual(list(frame["cid"]), ["m1:0", "m2:0", "m3:0"])          # picks by rank, then runners
        self.assertEqual(list(frame["pick"]), [True, True, False])
        self.assertEqual(rv.topic_counts(frame)["innovation"], 2)
        with tempfile.TemporaryDirectory() as td:
            ov = Path(td) / "overrides.json"
            rows = frame.to_dict("records")
            rows[1]["pick"] = False                  # owner drops the second pick
            rows[2]["pick"], rows[2]["note"] = True, "promoted: great story"
            with unittest.mock.patch.object(rv, "load_state", return_value={}), \
                    unittest.mock.patch.object(rv, "save_state") as saved:
                res = rv.apply_review(run_id, rows, "prefer stories over frameworks", overrides_path=ov)
            self.assertEqual(res["picks"], 2)
            self.assertEqual(saved.call_args[0][0]["reviewed_until"], "2026-08-15")
            dec = {d["canonical"]: d for d in db.load_decisions("2026-08-08", "2026-08-15")}
            self.assertEqual(len(dec), 3)
            self.assertTrue(dec["https://site3.com/post-3"]["pick"])
            self.assertEqual(dec["https://site3.com/post-3"]["note"], "promoted: great story")
            self.assertTrue(db.get_review("2026-08-08", "2026-08-15")["comment"].startswith("prefer"))
            self.assertTrue(db.list_runs()[0]["reviewed"])
            # tally: fav 1/1 yes, meh 1/2 yes — no tier yet
            self.assertEqual(db.decision_tally()["meh@x.com"], (2, 1))
            # re-run the same window → new run, earlier ticks pre-fill the new frame
            run2 = self._store()
            frame2 = rv.review_frame(run2)
            self.assertEqual(list(frame2["pick"]), [True, False, True])
            self.assertEqual(list(frame2["note"])[2], "promoted: great story")
            # backtest-style run on another window adds decisions → tier flips
            for k, (s, e) in enumerate((("2026-07-04", "2026-07-11"), ("2026-07-11", "2026-07-18"))):
                db.save_decisions(s, e, [{"canonical": f"https://m.com/{k}", "sender_address": "MEH@x.com", "pick": True}])
            changes = fb.apply_tiers(db.decision_tally(), overrides_path=ov)
            self.assertEqual(json.loads(ov.read_text(encoding="utf-8"))["senders"]["meh@x.com"]["tier"], "usually")
            self.assertTrue(any("meh@x.com" in c for c in changes))

    def test_feedback_cli_path_saves_to_store(self) -> None:
        self._store()
        with tempfile.TemporaryDirectory() as td:
            ov = Path(td) / "overrides.json"
            res = fb.apply([{"cid": "m1:0", "sender_address": "fav@x.com", "pick": True, "title": "t",
                             "url": "https://site1.com/post-1", "canonical": "https://site1.com/post-1"}],
                           window=("2026-08-08", "2026-08-15"), overrides_path=ov)
            self.assertEqual(res["saved"], 1)
            self.assertEqual(db.decision_tally()["fav@x.com"], (1, 1))
            dry = fb.apply([{"cid": "x", "sender_address": "fav@x.com", "pick": True, "canonical": "https://site9.com/p"}],
                           window=("2026-08-15", "2026-08-22"), overrides_path=ov, dry_run=True)
            self.assertEqual(dry["saved"], 0)
            self.assertEqual(db.decision_tally()["fav@x.com"], (1, 1))     # dry run stored nothing

    def test_import_history_and_next_edition(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            h = Path(td)
            (h / "editions.jsonl").write_text(
                json.dumps({"number": "N226", "date": "2026-08-01", "title": "A. B. C.", "must_read_title": "A",
                            "substack": "https://x.substack.com/p/a", "n_leader": 8, "n_innov": 8, "n_persdev": 8}) + "\n"
                + json.dumps({"number": "N227", "date": "2026-08-08", "title": "D. E. F."}) + "\n", encoding="utf-8")
            (h / "positives.jsonl").write_text(
                json.dumps({"article_id": "a1", "title": "A", "url": "https://a.com/1", "canonical": "https://a.com/1",
                            "domain": "a.com", "topic": "innovation", "author": "X", "star": True, "must_read": True,
                            "edition": "N226", "created": "2026-07-28", "summary": "s"}) + "\n"
                + json.dumps({"article_id": "a2", "title": "B", "url": "https://b.com/2", "canonical": "https://b.com/2",
                              "edition": "N999", "topic": "personal development"}) + "\n", encoding="utf-8")
            res = db.import_history(h)
        self.assertEqual(res, {"editions": 2, "picks": 2})
        picks = {r["article_id"]: r for r in self.fake.tables["triage_picks"]}
        self.assertTrue(picks["a1"]["star"] and picks["a1"]["must_read"])
        self.assertIsNone(picks["a2"]["edition"])           # unknown edition → no dangling FK
        self.assertEqual(db.next_edition_number(), "N228")

    def test_lessons_accept_and_export(self) -> None:
        run_id = self._store()
        rows = db.add_lessons(["Prefer first-person stories over listicles", "  "], run_id=run_id,
                              start="2026-08-08", end="2026-08-15", model="claude_sonnet")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["accepted"])
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "lessons.json"
            with unittest.mock.patch.object(ls, "LESSONS_PATH", out):
                ls.accept([rows[0]["id"]])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["lessons"][0]["text"], "Prefer first-person stories over listicles")
            self.assertEqual(data["lessons"][0]["window"], "2026-08-08→2026-08-15")
            from newsletter.triage import score as sc  # noqa: PLC0415
            self.assertEqual(sc.load_lessons(out), ["Prefer first-person stories over listicles"])
            with unittest.mock.patch.object(sc, "LESSONS_PATH", out):
                self.assertIn("Prefer first-person stories", sc._criteria_brief({"rules": {}}))
        # run replaced → lesson survives with run_id = null
        self._store()
        self.assertIsNone(self.fake.tables["triage_lessons"][0]["run_id"])
        self.assertEqual(len(db.lessons(accepted=True)), 1)

    def test_lessons_prompt_deltas(self) -> None:
        cands = [{"canonical": "https://a/1", "title": "Kept", "domain": "a", "topic": "innovation", "score": 9, "suggested": "pick"},
                 {"canonical": "https://a/2", "title": "Dropped", "domain": "a", "topic": "innovation", "score": 8, "suggested": "pick",
                  "reason": "fits the theme"},
                 {"canonical": "https://a/3", "title": "Promoted", "domain": "a", "topic": "innovation", "score": 5, "suggested": "runner"}]
        decisions = [{"canonical": "https://a/1", "pick": True}, {"canonical": "https://a/2", "pick": False},
                     {"canonical": "https://a/3", "pick": True, "note": "real-world case"}]
        d = ls.review_deltas(cands, decisions)
        self.assertEqual(len(d["dropped"]), 1)
        self.assertIn("Dropped", d["dropped"][0])
        self.assertEqual(len(d["promoted"]), 1)
        self.assertEqual(len(d["noted"]), 1)
        prompt = ls.build_prompt({"rules": {}}, "more cases, fewer frameworks", d)
        self.assertIn("more cases, fewer frameworks", prompt)
        self.assertIn("DROPPED", prompt)
        self.assertIn('{"lessons"', prompt)

    def test_new_senders_and_manual_tier(self) -> None:
        run_id = self._store()
        with tempfile.TemporaryDirectory() as td:
            ov = Path(td) / "overrides.json"
            with unittest.mock.patch.object(rv, "load_overrides", return_value={"senders": {}}):
                new = rv.new_senders(run_id)
            self.assertEqual([n["sender_address"] for n in new], ["meh@x.com"])
            self.assertEqual(new[0]["tier"], "review")
            rv.set_sender_tier("MEH@x.com", "usually", name="S3", overrides_path=ov)
            data = json.loads(ov.read_text(encoding="utf-8"))
            self.assertEqual(data["senders"]["meh@x.com"]["source"], "manual")
            rv.set_sender_tier("meh@x.com", "review", overrides_path=ov)     # back to default = entry removed
            self.assertNotIn("meh@x.com", json.loads(ov.read_text(encoding="utf-8"))["senders"])
            with self.assertRaises(ValueError):
                rv.set_sender_tier("a@b.c", "sometimes", overrides_path=ov)

    def test_saturday_weeks(self) -> None:
        weeks = rv.saturday_weeks(3, today=date(2026, 8, 21))       # a Friday
        self.assertEqual(weeks[0][0], date(2026, 8, 15))             # open week since last Saturday
        self.assertEqual(weeks[0][1], date(2026, 8, 22))
        self.assertEqual((weeks[1][0], weeks[1][1]), (date(2026, 8, 8), date(2026, 8, 15)))
        self.assertEqual((weeks[3][0], weeks[3][1]), (date(2026, 7, 25), date(2026, 8, 1)))
        self.assertEqual(rv.last_saturday(date(2026, 8, 22)), date(2026, 8, 22))


@unittest.skipUnless(os.environ.get("TRIAGE_DB_INTEGRATION") == "1", "set TRIAGE_DB_INTEGRATION=1 to probe the real project")
class IntegrationTests(unittest.TestCase):
    def test_schema_present(self) -> None:
        db.set_client(None)
        db.ensure_schema()
        self.assertIn("triage_runs", db.table_counts())


if __name__ == "__main__":
    unittest.main()
