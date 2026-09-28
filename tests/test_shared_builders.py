"""Shared supabase key-fallback builder, editorial Notion config, article normaliser base (issue #322).

Each of these replaced two-to-four hand-rolled copies that had already started
to diverge. No network: ``supabase.create_client`` is a fake, Notion calls are
patched.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from config.supabase_client import build_supabase_client
from newsletter import _normalizer_base as nb
from newsletter.normalize_names import NotionNameNormalizer
from newsletter.normalize_url import NotionURLNormalizer
from reporting.notion import _client as notion_client_mod


class _FakeSupabase:
    """``create_client(url, key)`` whose probe fails for keys listed in ``bad``."""

    def __init__(self, bad: dict[str, Exception]):
        self.bad = bad
        self.created: list[str] = []

    def install(self):
        fake = types.ModuleType("supabase")
        fake.create_client = self._create
        return mock.patch.dict(sys.modules, {"supabase": fake})

    def _create(self, url, key):
        self.created.append(key)
        err = self.bad.get(key)

        def execute():
            if err:
                raise err
            return None

        chain = mock.Mock()
        chain.table.return_value.select.return_value.limit.return_value.execute = execute
        chain.key = key
        return chain


CFG = {"url": "https://x.supabase.co", "service_role_key": "svc", "key": "k", "anon_key": "anon"}


class BuildSupabaseClientTests(unittest.TestCase):
    def test_first_working_key_wins(self):
        fake = _FakeSupabase({})
        with fake.install():
            client = build_supabase_client(CFG, probe_table="t", probe_column="c")
        self.assertEqual(client.key, "svc")
        self.assertEqual(fake.created, ["svc"])

    def test_failing_key_falls_through(self):
        fake = _FakeSupabase({"svc": RuntimeError("Invalid API key")})
        with fake.install():
            client = build_supabase_client(CFG, probe_table="t", probe_column="c")
        self.assertEqual(client.key, "k")

    def test_anon_fallback_warns(self):
        fake = _FakeSupabase({"svc": RuntimeError("nope"), "k": RuntimeError("nope")})
        with fake.install(), self.assertLogs("config.supabase_client", level="WARNING") as logs:
            client = build_supabase_client(CFG, probe_table="t", probe_column="c")
        self.assertEqual(client.key, "anon")
        self.assertIn("anon_key", logs.output[0])

    def test_error_accepted_by_predicate_keeps_the_key(self):
        """triage: 'table missing' proves the key is fine, so it is not skipped."""
        fake = _FakeSupabase({"svc": RuntimeError("PGRST205 table missing")})
        with fake.install():
            client = build_supabase_client(
                CFG, probe_table="t", probe_column="c",
                key_ok_despite_error=lambda err: "PGRST205" in str(err))
        self.assertEqual(client.key, "svc")

    def test_no_working_key_raises_with_last_error(self):
        fake = _FakeSupabase({k: RuntimeError("boom") for k in ("svc", "k", "anon")})
        with fake.install(), self.assertRaisesRegex(RuntimeError, "No working supabase key.*boom"):
            build_supabase_client(CFG, probe_table="t", probe_column="c")

    def test_missing_url_raises(self):
        with _FakeSupabase({}).install(), self.assertRaisesRegex(RuntimeError, "supabase.url"):
            build_supabase_client({"key": "k"}, probe_table="t", probe_column="c")


class TriageKeyAcceptanceTests(unittest.TestCase):
    def test_matches_the_original_fall_through_rule(self):
        from newsletter.triage import db

        self.assertFalse(db._key_accepted(RuntimeError("Invalid API key")))
        self.assertFalse(db._key_accepted(RuntimeError("401 Unauthorized")))
        self.assertTrue(db._key_accepted(RuntimeError("PGRST205 Could not find the table")))
        self.assertTrue(db._key_accepted(RuntimeError("some other transient error")))


class LoadEditorialNotionTests(unittest.TestCase):
    def _cfg(self, notion: dict):
        return mock.patch.object(notion_client_mod, "load_full_config", return_value={"notion": notion})

    def test_first_database_is_the_editorial_one(self):
        with self._cfg({"api_token": "tok", "databases": [{"id": "db1"}, {"id": "db2"}]}):
            self.assertEqual(notion_client_mod.load_editorial_notion(), ("tok", "db1"))

    def test_override_wins(self):
        with self._cfg({"api_token": "tok"}):
            self.assertEqual(notion_client_mod.load_editorial_notion("other"), ("tok", "other"))

    def test_missing_token_or_databases_raise(self):
        with self._cfg({"databases": [{"id": "db1"}]}), self.assertRaisesRegex(ValueError, "api_token"):
            notion_client_mod.load_editorial_notion()
        with self._cfg({"api_token": "tok"}), self.assertRaisesRegex(ValueError, "databases"):
            notion_client_mod.load_editorial_notion()

    def test_notion_block_defaults_to_empty(self):
        with mock.patch.object(notion_client_mod, "load_full_config", return_value={}):
            self.assertEqual(notion_client_mod.load_notion_block(), {})


class _Recorder(nb.NotionArticleNormalizer):
    """Minimal subclass: upper-cases the value of a ``name`` title property."""

    def __init__(self):
        self.database_id = "db"
        self.client = object()

    def _extract_page_info(self, page):
        return (page["id"], "t", page["name"]) if page.get("name") else None

    def _transform(self, original):
        return original.upper()

    def _patch_properties(self, new_value):
        return {"name": new_value}

    def _result_row(self, page_id, last_edited, original, new_value):
        return {"id": page_id, "new": new_value}


class NormalizerBaseTests(unittest.TestCase):
    PAGES = [{"id": "a", "name": "already"}, {"id": "b", "name": "ALREADY"}, {"id": "c"}]

    def _process(self, dry_run: bool, *, update_ok: bool = True, note=None):
        n = _Recorder()
        with mock.patch.object(nb.notion_io, "query_database", return_value=self.PAGES), \
                mock.patch.object(nb.notion_io, "update_page",
                                  side_effect=None if update_ok else RuntimeError("api")) as upd:
            results = n.process_database(3, dry_run=dry_run, note=note)
        return results, upd

    def test_live_updates_only_changed_rows_and_skips_unreadable_pages(self):
        results, upd = self._process(False)
        self.assertEqual(results, [{"id": "a", "new": "ALREADY"}, {"id": "b", "new": "ALREADY"}])
        upd.assert_called_once()
        self.assertEqual(upd.call_args.args[1:], ("a", {"name": "ALREADY"}))

    def test_dry_run_never_writes(self):
        _, upd = self._process(True)
        upd.assert_not_called()

    def test_update_failure_is_logged_not_raised(self):
        with self.assertLogs(level="ERROR") as logs:
            results, _ = self._process(False, update_ok=False)
        self.assertEqual(len(results), 2)
        self.assertTrue(any("Failed to update" in m for m in logs.output))

    def test_note_suffix_is_appended_to_log_lines(self):
        with self.assertLogs(level="INFO") as logs:
            self._process(True, note=lambda v: f" [{v}!]")
        self.assertTrue(any('"ALREADY" [ALREADY!]' in m for m in logs.output))

    def test_notion_query_error_propagates(self):
        with mock.patch.object(nb.notion_io, "query_database", side_effect=RuntimeError("down")), \
                self.assertRaisesRegex(RuntimeError, "down"):
            _Recorder().process_database(1)


class ConcreteNormalizerHooksTests(unittest.TestCase):
    """The two real normalisers keep their result shape and Notion payload."""

    def _bare(self, cls):
        return cls.__new__(cls)

    def test_url_hooks(self):
        n = self._bare(NotionURLNormalizer)
        n.domains_preserving_params = {"youtube.com"}
        self.assertEqual(n._transform("https://a.com/p?utm_source=x#frag"), "https://a.com/p")
        self.assertEqual(n._transform("https://youtube.com/watch?v=1"), "https://youtube.com/watch?v=1")
        self.assertEqual(n._patch_properties("u"), {"link": {"url": "u"}})
        self.assertEqual(n._result_row("id", "t", "o", "n"),
                         {"page_id": "id", "original_url": "o", "cleaned_url": "n"})

    def test_name_hooks(self):
        n = self._bare(NotionNameNormalizer)
        self.assertEqual(n._patch_properties("Hi"),
                         {"article": {"title": [{"type": "text", "text": {"content": "Hi"}}]}})
        self.assertEqual(n._result_row("id", "t", "o", "n"), {
            "page_id": "id", "last_edited_time": "t", "original_name": "o", "normalized_name": "n"})

    def test_url_testing_mode_builds_a_validity_note(self):
        n = self._bare(NotionURLNormalizer)
        with mock.patch.object(n, "_check_url_validity", return_value=(True, "OK (200)")), \
                mock.patch.object(nb.NotionArticleNormalizer, "process_database",
                                  return_value=[]) as base:
            n.process_database(5, dry_run=True, testing_mode=True)
            self.assertEqual(base.call_args.kwargs["note"]("u"), " [✅ OK (200)]")
        with mock.patch.object(nb.NotionArticleNormalizer, "process_database", return_value=[]) as base:
            n.process_database(5, dry_run=True)
        self.assertIsNone(base.call_args.kwargs["note"])


if __name__ == "__main__":
    unittest.main()
