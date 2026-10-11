"""The triage LLM cache is keyed by the model version the hub serves, and a re-score can bypass it (#398).

A fake hub speaks the two endpoints the triage uses (``GET /v1/models``, ``POST /v1/messages``), lists its
Claude rows the way the real one does (alias, then ``display_name``, then the older aliases) and counts the
Stage A calls it receives. The re-score body then runs end to end against it with a real ``LLMCache`` on a
temp file, so what is asserted is "did the model get called", not what a mock was told.

Covers: ``llm.served_model`` reading the version off the model list (and saying ``None`` when it can't);
same version → answers reused; the alias repointed to a newer model → nothing reused; ``fresh`` → every
call goes out and the new answers are stored; an unreadable version → keyed by the alias; the ``--fresh``
flag (the Triage tab switch is covered in ``test_triage_rescore``).

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_triage_cache_version -v
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter import llm  # noqa: E402
from newsletter.topics import TOPICS  # noqa: E402
from newsletter.triage import run  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402
from tests import test_triage_rescore as rs  # noqa: E402

MODELS_OLD = ["claude_haiku", "claude-haiku-4-5", "claude_sonnet", "claude-sonnet-4-6"]
MODELS_NEW = ["claude_haiku", "claude-haiku-5-5", "claude-haiku-4-5", "claude_sonnet", "claude-sonnet-5-5",
              "claude-sonnet-4-6"]


class FakeHub:
    """``models`` is mutable: swap it to repoint the alias mid-test."""

    def __init__(self, models: List[str]) -> None:
        self.models = models
        self.stage_a_calls = 0
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                pass

            def _send(self, body: Dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                self._send({"object": "list", "data": [{"id": m, "object": "model", "backend": "claude"}
                                                       for m in hub.models]})

            def do_POST(self) -> None:  # noqa: N802
                req = json.loads(self.rfile.read(int(self.headers["content-length"])))
                prompt = req["messages"][0]["content"]
                n = len(re.findall(r"^\d+\. sender=", prompt, flags=re.M))
                if n:
                    hub.stage_a_calls += 1
                    text = json.dumps([{"i": i + 1, "topic": TOPICS[0], "fit": 4, "news": False, "promo": False,
                                        "reason": "fits"} for i in range(n)])
                else:
                    text = "ok"
                self._send({"id": "msg_1", "type": "message", "role": "assistant", "model": req["model"],
                            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class ServedModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = FakeHub(MODELS_NEW)
        self.addCleanup(self.hub.close)

    def test_reads_the_version_behind_the_alias(self) -> None:
        self.assertEqual(llm.served_model(base_url=self.hub.url, model="claude_haiku"), "claude-haiku-5-5")
        self.assertEqual(llm.served_model(base_url=self.hub.url, model="claude_sonnet"), "claude-sonnet-5-5")
        self.hub.models = MODELS_OLD
        self.assertEqual(llm.served_model(base_url=self.hub.url, model="claude_haiku"), "claude-haiku-4-5")

    def test_a_versioned_id_is_its_own_version_without_asking_the_hub(self) -> None:
        self.assertEqual(llm.served_model(base_url="http://127.0.0.1:1", model="claude-haiku-4-5"),
                         "claude-haiku-4-5")

    def test_unknown_when_it_cannot_be_established(self) -> None:
        self.assertIsNone(llm.served_model(base_url=self.hub.url, model="not_a_model"))
        self.hub.models = ["claude_haiku", "gemini_pro"]          # the next id doesn't extend the alias
        self.assertIsNone(llm.served_model(base_url=self.hub.url, model="claude_haiku"))
        self.assertIsNone(llm.served_model(base_url="http://127.0.0.1:1", model="claude_haiku", timeout=2))


class RescoreCacheTests(unittest.TestCase):
    """The re-score body against the fake hub, a real LLMCache on a temp file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.hub = FakeHub(MODELS_OLD)
        self.addCleanup(self.hub.close)

    def _rescore(self, **extra: Any) -> Dict[str, Any]:
        """One re-score of the stored week through the real body; → its stats."""
        self.hub.stage_a_calls = 0
        cache_path = self.tmp / "llm_cache.json"

        class TmpCache(sc.LLMCache):      # the real cache on a temp file; a subclass so ``LLMCache.key`` still resolves
            def __init__(self, **kw: Any) -> None:
                super().__init__(cache_path, **kw)

        with mock.patch.object(run, "load_notion_urls", return_value=(set(), {})), \
                mock.patch.object(run, "store_ok", return_value=False), \
                mock.patch.object(run.sc, "LLMCache", TmpCache), \
                mock.patch.object(run.sc, "Priors", return_value=rs._priors()):   # noqa: SLF001
            _p, _s, stats, _c, _e = run._run_window_body(   # noqa: SLF001
                date(2026, 10, 3), date(2026, 10, 10), cfg={"llm_hub_base_url": self.hub.url}, criteria=rs.CRITERIA,
                source="store", use_llm=True, use_fetch=False, top_k=10, model="claude_haiku", out_dir=self.tmp,
                edition_hint="next", backtest=None, limit_links=None, engine="legacy", inputs=rs._doc(), **extra)  # noqa: SLF001
        return stats

    def test_same_version_reuses_the_answers(self) -> None:
        self._rescore()
        self.assertEqual(self.hub.stage_a_calls, 1)
        self._rescore()
        self.assertEqual(self.hub.stage_a_calls, 0)

    def test_a_repointed_alias_never_replays_the_older_models_answers(self) -> None:
        self._rescore()                                          # scored while the alias meant 4.5
        self.assertEqual(self.hub.stage_a_calls, 1)
        self.hub.models = MODELS_NEW                             # the hub now serves claude_haiku as 5.5
        stats = self._rescore()
        self.assertEqual(self.hub.stage_a_calls, 1, "the 4.5 answers were replayed for a 5.5 re-score")
        self.assertEqual(stats["llm_model_served"], "claude-haiku-5-5")
        self._rescore()                                          # and 5.5's own answers are reused from then on
        self.assertEqual(self.hub.stage_a_calls, 0)

    def test_fresh_calls_the_model_and_still_stores_the_answers(self) -> None:
        self._rescore()
        stats = self._rescore(fresh=True)
        self.assertEqual(self.hub.stage_a_calls, 1)
        self.assertTrue(stats["llm_cache_fresh"])
        self._rescore()                                          # not fresh again: the stored answers are reused
        self.assertEqual(self.hub.stage_a_calls, 0)

    def test_an_unreadable_version_falls_back_to_the_alias_and_says_so(self) -> None:
        self.hub.models = ["claude_haiku"]
        with self.assertLogs("newsletter_triage.score", level="WARNING") as logs:
            stats = self._rescore()
        self.assertEqual(stats["llm_model_served"], "claude_haiku")
        self.assertTrue(any("version of 'claude_haiku' unknown" in m for m in logs.output))
        self._rescore()
        self.assertEqual(self.hub.stage_a_calls, 0)


class FreshFlagTests(unittest.TestCase):
    def _main(self, argv: List[str]) -> Any:
        with mock.patch.object(run, "load_block", return_value={}), \
                mock.patch.object(run, "load_criteria", return_value=rs.CRITERIA), \
                mock.patch.object(run, "rescore", return_value=("p", mock.MagicMock(), {"selected": 1, "store": "x"})) as r:
            code = run.main(argv)
        return code, r

    def test_rescore_passes_fresh_on(self) -> None:
        code, r = self._main(["--rescore", "18", "--fresh"])
        self.assertEqual((code, r.call_args.kwargs["fresh"]), (0, True))

    def test_rescore_is_not_fresh_by_default(self) -> None:
        _code, r = self._main(["--rescore", "18"])
        self.assertFalse(r.call_args.kwargs["fresh"])

    def test_fresh_without_rescore_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr"):
            run.main(["--fresh"])
        self.assertEqual(ctx.exception.code, 2)

    def test_rescore_hands_fresh_to_the_window(self) -> None:
        with mock.patch.object(run, "store_ok", return_value=True), \
                mock.patch.object(run.db, "get_run", return_value=rs.RUN), \
                mock.patch.object(run, "stored_inputs", return_value=rs._doc()), \
                mock.patch.object(run, "run_window", return_value=("p", "s", {})) as rw:   # noqa: SLF001
            run.rescore(18, cfg={}, criteria=rs.CRITERIA, use_llm=True, use_fetch=True, top_k=90,
                        model="claude_haiku", out_dir=Path("."), fresh=True)
        self.assertTrue(rw.call_args.kwargs["fresh"])


if __name__ == "__main__":
    unittest.main()
