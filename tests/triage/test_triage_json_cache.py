"""The shared locked JSON-file cache under the triage Fetch/LLM/Redirect caches (issue #371)."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from newsletter.triage import fetch as fx
from newsletter.triage import gmail as gm
from newsletter.triage import score as sc
from newsletter.triage.json_cache import JsonFileCache


class JsonFileCacheTests(unittest.TestCase):
    def test_all_three_caches_share_the_base(self) -> None:
        for cls in (fx.FetchCache, sc.LLMCache, gm.RedirectCache):
            self.assertTrue(issubclass(cls, JsonFileCache), cls)

    def test_flush_round_trips_through_a_new_instance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "redirects.json"
            cache = gm.RedirectCache(path)
            cache.put("a", "https://example.com/a")
            cache.flush()
            self.assertFalse(path.with_suffix(".tmp").exists())
            again = gm.RedirectCache(path)
            self.assertEqual((again.get("a"), len(again)), ("https://example.com/a", 1))

    def test_redirect_cache_auto_flushes_every_200_puts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "redirects.json"
            cache = gm.RedirectCache(path)
            for i in range(199):
                cache.put(str(i), "x")
            self.assertFalse(path.exists())
            cache.put("199", "x")
            self.assertEqual(len(gm.RedirectCache(path)), 200)

    def test_corrupt_file_starts_empty_with_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "llm_cache.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertLogs("newsletter_triage.json_cache", logging.WARNING) as logs:
                cache = sc.LLMCache(path)
            self.assertEqual(len(cache), 0)
            self.assertIn("llm cache unreadable", logs.output[0])

    def test_llm_cache_counts_hits_and_misses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = sc.LLMCache(Path(d) / "c.json")
            k = sc.LLMCache.key("m", "A", "x")
            self.assertIsNone(cache.get(k))
            cache.put(k, {"v": 1})
            self.assertEqual(cache.get(k), {"v": 1})
            self.assertEqual((cache.hits, cache.misses), (1, 1))


if __name__ == "__main__":
    unittest.main()
