"""Re-score a stored triage run with a chosen Stage A engine (#395). No network: the store, the hub and
Notion are patched; Gmail and the history cache must never be read on the re-score path.

Covers: stored rows rebuild the candidates in inbox order with today's priors and the original dedupe
flags; the inputs are snapshotted before the run is replaced and a run left empty by a failed re-score
falls back to the snapshot; the body never reads e-mail and records ``rescored_from``; ``--rescore`` only
takes live runs, replaces with ``--force`` semantics and leaves ``state.json`` alone; the engine label
the tab shows; and the tab launches the re-score with Jev pre-selected.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.triage.test_triage_rescore -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.topics import TOPICS  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage import review  # noqa: E402
from newsletter.triage import run  # noqa: E402

RUN = {"id": 18, "window_start": "2026-10-03", "window_end": "2026-10-10", "kind": "live", "status": "done",
       "stats": {"stage_a_engine": "both", "links": 3}}
EMAILS = [{"message_id": "m2", "sender_name": "Late", "sender_address": "late@x.com", "subject": "b",
           "ts": "2026-10-05T09:00:00+00:00"},
          {"message_id": "m1", "sender_name": "Early", "sender_address": "early@x.com", "subject": "a",
           "ts": "2026-10-04T09:00:00+00:00"}]
CANDS = [{"cid": "m2:0", "message_id": "m2", "url": "https://b.com/p", "canonical": "https://b.com/p",
          "domain": "b.com", "title": "A stored page title", "in_notion": False, "reason": None,
          "sender_weight": 0.1, "meta": {"ok": True}},
         {"cid": "m1:3", "message_id": "m1", "url": "https://r.example/x", "canonical": "https://r.example/x",
          "domain": "r.example", "title": "Unresolved anchor", "in_notion": False, "reason": "redirect unresolved"},
         {"cid": "m1:1", "message_id": "m1", "url": "https://a.com/q", "canonical": "https://a.com/q",
          "domain": "a.com", "title": "Already in Notion", "in_notion": True, "reason": "already in Notion"}]
CRITERIA = {"rules": {"topics": {t: {"themes": ["x"], "anti_themes": []} for t in TOPICS}}}


def _priors() -> mock.MagicMock:
    p = mock.MagicMock(ranked={})
    p.sender.side_effect = lambda addr: (0.8, "ranked", False) if addr == "late@x.com" else (1.0, "override", False)
    p.domain.return_value = (0.25, 3)
    p.topic_prior.return_value = TOPICS[2]
    return p


def _doc() -> Dict[str, Any]:
    return {"run_id": 18, "window": ["2026-10-03", "2026-10-10"], "emails": EMAILS, "candidates": CANDS}


class CandidatesFromStoreTests(unittest.TestCase):
    def test_rebuilds_in_inbox_order_with_todays_priors(self) -> None:
        records, cands, by_email = run.candidates_from_store(_doc(), _priors())
        self.assertEqual([r.message_id for r in records], ["m1", "m2"])
        self.assertEqual([c.cid for c in cands], ["m1:1", "m1:3", "m2:0"])
        late = cands[2]
        self.assertEqual((late.label, late.sender_name, late.subject), ("A stored page title", "Late", "b"))
        self.assertEqual((late.sender_weight, late.sender_basis, late.domain_bonus), (0.8, "ranked", 0.25))
        self.assertEqual(late.title, "")                       # stage B may fetch the page title again
        self.assertEqual(sorted(by_email), ["m1", "m2"])
        self.assertEqual([c.cid for c in by_email["m1"]], ["m1:1", "m1:3"])

    def test_keeps_the_original_dedupe_and_rechecks_earlier_picks(self) -> None:
        _r, cands, _b = run.candidates_from_store(_doc(), _priors(),
                                                  {"https://b.com/p": "2026-09-26 → 2026-10-03"})
        by_cid = {c.cid: c for c in cands}
        self.assertTrue(by_cid["m1:1"].in_notion)
        self.assertEqual(by_cid["m1:1"].picked_earlier, "")
        self.assertEqual(by_cid["m1:3"].reason, "redirect unresolved")
        self.assertEqual((by_cid["m2:0"].in_notion, by_cid["m2:0"].picked_earlier),
                         (True, "2026-09-26 → 2026-10-03"))


class StoredInputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(run, "INPUTS_DIR", Path(self._tmp.name) / "inputs")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_snapshots_the_stored_rows_before_the_replace(self) -> None:
        with mock.patch.object(run.db, "candidates", return_value=CANDS), \
                mock.patch.object(run.db, "emails", return_value=EMAILS):
            doc = run.stored_inputs(RUN)
        self.assertEqual((doc["run_id"], doc["candidates"], doc["emails"]), (18, CANDS, EMAILS))
        snap = run.INPUTS_DIR / "triage-2026-10-03_2026-10-10.inputs.json"
        self.assertEqual(json.loads(snap.read_text(encoding="utf-8")), doc)

    def test_a_run_emptied_by_a_failed_rescore_falls_back_to_the_snapshot(self) -> None:
        with mock.patch.object(run.db, "candidates", return_value=CANDS), \
                mock.patch.object(run.db, "emails", return_value=EMAILS):
            first = run.stored_inputs(RUN)
        with mock.patch.object(run.db, "candidates", return_value=[]), \
                mock.patch.object(run.db, "emails", return_value=[]):
            again = run.stored_inputs(dict(RUN, id=19, status="failed"))
        self.assertEqual(again, first)

    def test_no_rows_and_no_snapshot_is_its_own_error(self) -> None:
        with mock.patch.object(run.db, "candidates", return_value=[]):
            with self.assertRaises(run.NoStoredInputs) as ctx:
                run.stored_inputs(RUN)
        self.assertIn("re-run the week from Gmail", str(ctx.exception))


class RescoreBodyTests(unittest.TestCase):
    def test_reads_no_email_and_scores_with_the_chosen_engine(self) -> None:
        jev = mock.MagicMock(side_effect=lambda items, *a, **k: [
            jx.JevMetaScore(topic=TOPICS[0], fit=4, ok=True) for _ in items])
        legacy = mock.MagicMock()
        no_mail = mock.MagicMock(side_effect=AssertionError("e-mail read on the re-score path"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run, "emails_from_gmail", no_mail), \
                mock.patch.object(run, "emails_from_cache", no_mail), \
                mock.patch.object(run, "load_notion_urls", return_value=(set(), {})) as notion, \
                mock.patch.object(run, "store_ok", return_value=False), \
                mock.patch.object(run.llm, "health_check", return_value=True), \
                mock.patch.object(run.llm, "served_model", return_value="claude-haiku-5-5"), \
                mock.patch.object(run.sc, "score_metadata", legacy), \
                mock.patch.object(run.sc, "LLMCache", return_value=mock.MagicMock(misses=0)), \
                mock.patch.object(run.sc, "Priors", return_value=_priors()), \
                mock.patch.object(jx, "score_metadata_jev", jev):
            path, sel, stats, cands, emails = run._run_window_body(
                date(2026, 10, 3), date(2026, 10, 10), cfg={"llm_hub_base_url": "http://hub"}, criteria=CRITERIA,
                source="store", use_llm=True, use_fetch=False, top_k=10, model="claude_haiku", out_dir=Path(tmp),
                edition_hint="next", backtest=None, limit_links=None, engine="jev", inputs=_doc())
            self.assertTrue(path.exists())
        notion.assert_called_once_with(created_before="2026-10-03")
        legacy.assert_not_called()
        items = jev.call_args.args[0]
        self.assertEqual([i["label"] for i in items], ["Unresolved anchor", "A stored page title"])
        self.assertEqual((stats["stage_a_engine"], stats["rescored_from"], stats["emails"], stats["links"]),
                         ("jev", 18, 2, 3))
        self.assertEqual([e["message_id"] for e in emails], ["m1", "m2"])


class RescoreEntryTests(unittest.TestCase):
    def test_replaces_the_window_from_stored_inputs(self) -> None:
        with mock.patch.object(run, "store_ok", return_value=True), \
                mock.patch.object(run.db, "get_run", return_value=RUN), \
                mock.patch.object(run, "stored_inputs", return_value=_doc()), \
                mock.patch.object(run, "run_window", return_value=("p", "s", {})) as rw:
            run.rescore(18, cfg={}, criteria=CRITERIA, use_llm=True, use_fetch=True, top_k=90, model="claude_haiku",
                        out_dir=Path("."), engine="jev")
        args, kwargs = rw.call_args
        self.assertEqual(args, (date(2026, 10, 3), date(2026, 10, 10)))
        self.assertEqual((kwargs["source"], kwargs["force"], kwargs["engine"], kwargs["inputs"]),
                         ("store", True, "jev", _doc()))

    def test_refuses_a_backtest_run(self) -> None:
        with mock.patch.object(run, "store_ok", return_value=True), \
                mock.patch.object(run.db, "get_run", return_value=dict(RUN, kind="backtest")), \
                mock.patch.object(run, "run_window") as rw:
            with self.assertRaises(run.NoStoredInputs):
                run.rescore(7, cfg={}, criteria=CRITERIA, use_llm=True, use_fetch=True, top_k=90,
                            model="claude_haiku", out_dir=Path("."))
        rw.assert_not_called()

    def _main(self, argv: List[str], **rescore: Any) -> Any:
        with mock.patch.object(run, "load_block", return_value={"stage_a_engine": "both"}), \
                mock.patch.object(run, "load_criteria", return_value=CRITERIA), \
                mock.patch.object(run, "save_state") as save_state, \
                mock.patch.object(run, "rescore", **rescore) as rs:
            code = run.main(argv)
        return code, rs, save_state

    def test_cli_passes_the_engine_and_leaves_the_watermark_alone(self) -> None:
        code, rs, save_state = self._main(["--rescore", "18", "--stage-a-engine", "jev"],
                                          return_value=("p", mock.MagicMock(), {"selected": 24, "store": "run 19"}))
        self.assertEqual(code, 0)
        self.assertEqual((rs.call_args.args, rs.call_args.kwargs["engine"]), ((18,), "jev"))
        save_state.assert_not_called()

    def test_cli_missing_inputs_exit_4(self) -> None:
        code, _rs, _s = self._main(["--rescore", "18"], side_effect=run.NoStoredInputs("gone"))
        self.assertEqual(code, 4)


class RescoreStoreRoundTripTests(unittest.TestCase):
    """``rescore`` end to end against the in-memory store fake: the reviewed week is replaced, the owner's
    ticks + comment survive, and a re-score that dies after the replace is retried from the snapshot."""

    def setUp(self) -> None:
        from tests.triage.test_triage_db import FakeSupabase, _cand  # noqa: PLC0415

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        run.db.set_client(FakeSupabase())
        self.addCleanup(run.db.set_client, None)
        cands = [_cand(i, topic=TOPICS[2], score=9.0 - i, sender=f"s{i}@x.com") for i in (1, 2, 3)]
        sel = run.rk.select(cands, {}, per_topic=2, runners_n=2)
        self.run_id = run.db.register_run(date(2026, 10, 3), date(2026, 10, 10), source="gmail", model="claude_haiku",
                                          criteria_version="1")
        emails = [{"message_id": c.message_id, "sender_name": c.sender_name, "sender_address": c.sender_address,
                   "subject": "s", "timestamp": c.email_ts, "sender_basis": "ranked", "is_new": False} for c in cands]
        run.db.store_run_results(self.run_id, emails, run.db.candidate_rows(cands, sel))
        run.db.mark_run(self.run_id, status="done", stats={"stage_a_engine": "both", "stage_a_scored": 3})
        rows = review.review_frame(self.run_id).to_dict("records")
        rows[0]["pick"], rows[0]["note"] = False, "owner dropped it"
        with mock.patch.object(review, "load_state", return_value={}), mock.patch.object(review, "save_state"):
            review.apply_review(self.run_id, rows, "owner comment", overrides_path=Path(self._tmp.name) / "ov.json")
        for owner, target, value in ((run, "store_ok", mock.MagicMock(return_value=True)),
                                     (run, "INPUTS_DIR", Path(self._tmp.name)),
                                     (run, "load_notion_urls", mock.MagicMock(return_value=(set(), {}))),
                                     (run.llm, "health_check", mock.MagicMock(return_value=True)),
                                     (run.sc, "Priors", mock.MagicMock(return_value=_priors()))):
            p = mock.patch.object(owner, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _rescore(self, run_id: int, jev: mock.MagicMock) -> Any:
        with mock.patch.object(jx, "score_metadata_jev", jev), \
                mock.patch.object(run.llm, "served_model", return_value="claude-haiku-5-5"):
            return run.rescore(run_id, cfg={"llm_hub_base_url": "http://hub"}, criteria=CRITERIA, use_llm=True,
                               use_fetch=False, top_k=10, model="claude_haiku", out_dir=Path(self._tmp.name),
                               engine="jev")

    @staticmethod
    def _jev(items: List[Dict[str, Any]], *_a: Any, **_k: Any) -> List[jx.JevMetaScore]:
        return [jx.JevMetaScore(topic=TOPICS[0], fit=5, ok=True) for _ in items]

    def test_replaces_the_week_and_keeps_the_review(self) -> None:
        _p, _s, stats = self._rescore(self.run_id, mock.MagicMock(side_effect=self._jev))
        self.assertIsNone(run.db.get_run(self.run_id))
        new = run.db.list_runs()[0]
        self.assertEqual((new["status"], new["source"], new["reviewed"]), ("done", "store", True))
        self.assertEqual(review.engine_label(new["stats"]), "Jev")
        self.assertEqual(new["stats"]["rescored_from"], self.run_id)
        self.assertEqual({c["meta"]["topic"] for c in run.db.candidates(new["id"]) if c.get("meta")}, {TOPICS[0]})
        self.assertEqual(run.db.get_review("2026-10-03", "2026-10-10")["comment"], "owner comment")
        frame = review.review_frame(new["id"])
        dropped = frame[frame["canonical"] == "https://site1.com/post-1"].iloc[0]
        self.assertEqual((bool(dropped["pick"]), dropped["note"]), (False, "owner dropped it"))

    def test_a_rescore_that_dies_after_the_replace_retries_from_the_snapshot(self) -> None:
        with self.assertRaises(RuntimeError):
            self._rescore(self.run_id, mock.MagicMock(side_effect=RuntimeError("hub fell over")))
        failed = run.db.list_runs()[0]
        self.assertEqual((failed["status"], run.db.candidates(failed["id"])), ("failed", []))
        jev = mock.MagicMock(side_effect=self._jev)
        self._rescore(failed["id"], jev)
        self.assertEqual(len(jev.call_args.args[0]), 3)
        self.assertEqual(run.db.list_runs()[0]["status"], "done")


class EngineLabelTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(review.engine_label({"stage_a_engine": "jev", "stage_a_scored": 3}), "Jev")
        self.assertEqual(review.engine_label({"stage_a_engine": "both"}), "Claude Haiku + Jev shadow")
        self.assertEqual(review.engine_label({"stage_a_scored": 3, "scored": 3}), "Claude Haiku")   # pre-#285 legacy
        self.assertEqual(review.engine_label({"scored": 3}), "rule-only")
        self.assertEqual(review.engine_label({"stage_a_engine": "jev+legacy"}), "Jev + Claude Haiku shadow")
        self.assertEqual(review.engine_label({"stage_a_engine": "next-engine"}), "next-engine")     # unknown → raw
        self.assertEqual(review.engine_label(None), "")


def _tab_script() -> None:
    from app import tab_triage  # noqa: PLC0415

    tab_triage.run()


class TabTests(unittest.TestCase):
    def _app(self) -> tuple:
        week = review.saturday_weeks(8)[1]          # the tab's default window
        stored = dict(RUN, window_start=str(week[0]), window_end=str(week[1]), reviewed=True)
        from app import tab_triage  # noqa: PLC0415

        tab_triage._invalidate()                    # noqa: SLF001 — st.cache_data outlives a test
        self.addCleanup(tab_triage._invalidate)     # noqa: SLF001
        launched = mock.MagicMock()
        p = mock.patch.object(tab_triage.db, "list_runs", return_value=[stored])
        p.start()
        self.addCleanup(p.stop)
        for target, value in (("start_pipeline", launched), ("is_running", mock.MagicMock(return_value=False)),
                              ("render_log_panel", mock.MagicMock()), ("render_status_badge", mock.MagicMock())):
            p = mock.patch.object(tab_triage, target, value)
            p.start()
            self.addCleanup(p.stop)
        return AppTest.from_function(_tab_script).run(), launched

    def test_rescore_is_behind_the_replace_toggle_with_jev_preselected(self) -> None:
        at, launched = self._app()
        self.assertFalse(at.exception)
        self.assertEqual(at.radio(key="triage-engine").value, "jev+legacy")
        self.assertTrue(any("scored by Claude Haiku + Jev shadow" in c.value for c in at.caption))
        self.assertTrue(at.button(key="triage-rescore").disabled)
        at.toggle(key="triage-override").set_value(True).run()
        at.button(key="triage-rescore").click().run()
        self.assertFalse(at.exception)
        cmd = launched.call_args.args[1]
        self.assertEqual(cmd[1:], ["-m", "newsletter.triage.run", "--stage-a-engine", "jev+legacy", "--rescore", "18"])

    def test_fresh_switch_adds_the_flag_and_is_off_by_default(self) -> None:
        at, launched = self._app()
        at.toggle(key="triage-override").set_value(True).run()
        self.assertFalse(at.toggle(key="triage-fresh").value)
        at.toggle(key="triage-fresh").set_value(True).run()
        at.button(key="triage-rescore").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(launched.call_args.args[1][-3:], ["--rescore", "18", "--fresh"])

    def test_claude_haiku_passes_legacy(self) -> None:
        at, launched = self._app()
        at.radio(key="triage-engine").set_value("legacy").run()
        at.toggle(key="triage-override").set_value(True).run()
        self.assertEqual(at.button(key="triage-rescore").label, "↻ Re-score with Claude Haiku (no Gmail)")
        at.button(key="triage-rescore").click().run()
        self.assertIn("legacy", launched.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
