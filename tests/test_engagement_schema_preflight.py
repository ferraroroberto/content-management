"""Regression tests for the engagement schema preflight (issue #262).

`engagement/db/schema.sql` is applied **by hand** through the Supabase
dashboard SQL editor, so nothing stops code that writes a new column from
shipping before the column exists. That is exactly what happened: the
`post_posted_at` column added in `fe68ac5` never reached the live database, and
for seven days every scheduled LinkedIn scrape logged in, walked the posts,
extracted the comments — and then died on its final upsert with PGRST204,
throwing all of that work away. Twenty consecutive failed runs.

Two directions of drift, two guards:

* ``REQUIRED_COLUMNS`` vs ``schema.sql`` — caught here, by parsing the DDL.
* ``schema.sql`` vs the live database — caught at runtime by
  :func:`verify_schema`, whose branching this module also pins.

The parser is deliberately test-only: the runtime constant stays a plain
literal, so a schema check never depends on shipping (or parsing) a .sql file.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Dict, List, Set

from engagement.db.client import (
    REQUIRED_COLUMNS,
    SchemaDriftError,
    _missing_columns,
    verify_schema,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "engagement" / "db" / "schema.sql"

# Line-leading keywords inside a create-table body that introduce a constraint
# rather than a column.
_NOT_A_COLUMN = ("primary", "unique", "foreign", "constraint", "check")


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def parse_schema_columns(sql: str) -> Dict[str, Set[str]]:
    """Extract ``{table: {column, ...}}`` from the subset of DDL we author.

    Handles the two forms in `schema.sql`: the ``create table if not exists``
    body, and the idempotent ``alter table ... add column if not exists`` lines
    that carry columns added after the original revision.
    """
    sql = _strip_comments(sql)
    tables: Dict[str, Set[str]] = {}

    for match in re.finditer(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)\s*\((.*?)\n\);",
        sql,
        re.IGNORECASE | re.DOTALL,
    ):
        table, body = match.group(1), match.group(2)
        cols: Set[str] = set()
        for raw in body.split("\n"):
            token = raw.strip().strip(",").split()
            if not token:
                continue
            name = token[0].lower()
            if name.startswith(_NOT_A_COLUMN):
                continue
            cols.add(token[0])
        tables[table] = cols

    for table, column in re.findall(
        r"alter\s+table\s+(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)",
        sql,
        re.IGNORECASE,
    ):
        tables.setdefault(table, set()).add(column)

    return tables


class _FakeAPIError(Exception):
    """Stands in for ``postgrest.exceptions.APIError``."""


class _FakeQuery:
    def __init__(self, table: "_FakeTable", projection: str) -> None:
        self._table = table
        self._cols = [c.strip() for c in projection.split(",")]

    def limit(self, _n: int) -> "_FakeQuery":
        return self

    def execute(self) -> None:
        if self._table.transport_error:
            raise ConnectionError("connection reset")
        missing = [c for c in self._cols if c not in self._table.columns]
        if missing:
            raise _FakeAPIError(f"column {missing[0]} does not exist")


class _FakeTable:
    def __init__(self, columns: Set[str], transport_error: bool = False) -> None:
        self.columns = columns
        self.transport_error = transport_error
        self.projections: List[str] = []

    def select(self, projection: str) -> _FakeQuery:
        self.projections.append(projection)
        return _FakeQuery(self, projection)


class _FakeClient:
    def __init__(self, tables: Dict[str, _FakeTable]) -> None:
        self._tables = tables

    def table(self, name: str) -> _FakeTable:
        return self._tables[name]


def _patch_api_error(test: unittest.TestCase) -> None:
    """Make the lazily-imported ``APIError`` resolve to our fake."""
    import sys
    import types

    module = types.ModuleType("postgrest.exceptions")
    module.APIError = _FakeAPIError  # type: ignore[attr-defined]
    pkg = sys.modules.get("postgrest")
    created = pkg is None
    if created:
        pkg = types.ModuleType("postgrest")
        sys.modules["postgrest"] = pkg
    previous = sys.modules.get("postgrest.exceptions")
    sys.modules["postgrest.exceptions"] = module

    def restore() -> None:
        if previous is not None:
            sys.modules["postgrest.exceptions"] = previous
        else:
            sys.modules.pop("postgrest.exceptions", None)
        if created:
            sys.modules.pop("postgrest", None)

    test.addCleanup(restore)


class SchemaConstantMatchesDDL(unittest.TestCase):
    """REQUIRED_COLUMNS must not drift from schema.sql."""

    def setUp(self) -> None:
        self.parsed = parse_schema_columns(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_parser_finds_both_tables(self) -> None:
        # Guards the guard: a parser that silently matched nothing would make
        # every assertion below vacuously pass.
        self.assertEqual(set(self.parsed), {"commenters", "comments"})
        self.assertIn("post_posted_at", self.parsed["comments"])
        self.assertNotIn("primary", self.parsed["comments"])

    def test_every_required_column_exists_in_the_ddl(self) -> None:
        for table, columns in REQUIRED_COLUMNS.items():
            with self.subTest(table=table):
                self.assertEqual(
                    set(columns) - self.parsed[table],
                    set(),
                    f"{table}: REQUIRED_COLUMNS names columns absent from schema.sql",
                )

    def test_every_ddl_column_is_required(self) -> None:
        for table, columns in self.parsed.items():
            with self.subTest(table=table):
                self.assertEqual(
                    columns - set(REQUIRED_COLUMNS[table]),
                    set(),
                    f"{table}: schema.sql grew a column REQUIRED_COLUMNS does not list",
                )


class MissingColumnDetection(unittest.TestCase):
    def setUp(self) -> None:
        _patch_api_error(self)

    def test_happy_path_costs_one_request(self) -> None:
        cols = set(REQUIRED_COLUMNS["comments"])
        table = _FakeTable(cols)
        client = _FakeClient({"comments": table})
        self.assertEqual(
            _missing_columns(client, "comments", REQUIRED_COLUMNS["comments"]), []
        )
        self.assertEqual(len(table.projections), 1)

    def test_reports_every_missing_column_not_just_the_first(self) -> None:
        cols = set(REQUIRED_COLUMNS["comments"]) - {"post_posted_at", "my_replied_at"}
        client = _FakeClient({"comments": _FakeTable(cols)})
        self.assertEqual(
            sorted(_missing_columns(client, "comments", REQUIRED_COLUMNS["comments"])),
            ["my_replied_at", "post_posted_at"],
        )

    def test_transport_error_propagates_rather_than_faking_drift(self) -> None:
        table = _FakeTable(set(REQUIRED_COLUMNS["comments"]), transport_error=True)
        client = _FakeClient({"comments": table})
        with self.assertRaises(ConnectionError):
            _missing_columns(client, "comments", REQUIRED_COLUMNS["comments"])


class VerifySchemaBranches(unittest.TestCase):
    def setUp(self) -> None:
        _patch_api_error(self)
        import engagement.db.client as client_mod

        self.client_mod = client_mod

    def _with_client(self, factory) -> None:
        original = self.client_mod.supabase_client
        self.client_mod.supabase_client = factory
        self.addCleanup(lambda: setattr(self.client_mod, "supabase_client", original))

    def _full_client(self, drop: Dict[str, Set[str]] | None = None) -> _FakeClient:
        drop = drop or {}
        return _FakeClient(
            {
                name: _FakeTable(set(cols) - drop.get(name, set()))
                for name, cols in REQUIRED_COLUMNS.items()
            }
        )

    def test_ok_when_every_column_present(self) -> None:
        self._with_client(lambda: self._full_client())
        self.assertEqual(verify_schema(), "ok")

    def test_raises_on_real_drift_and_names_the_column(self) -> None:
        self._with_client(lambda: self._full_client({"comments": {"post_posted_at"}}))
        with self.assertRaises(SchemaDriftError) as ctx:
            verify_schema()
        message = str(ctx.exception)
        self.assertIn("post_posted_at", message)
        self.assertIn("comments", message)
        self.assertIn("schema.sql", message)

    def test_unreachable_client_reports_unverified_not_ok(self) -> None:
        def boom():
            raise RuntimeError("no working supabase key found")

        self._with_client(boom)
        self.assertEqual(verify_schema(), "unverified")

    def test_transport_failure_reports_unverified_not_drift(self) -> None:
        tables = {
            name: _FakeTable(set(cols), transport_error=True)
            for name, cols in REQUIRED_COLUMNS.items()
        }
        self._with_client(lambda: _FakeClient(tables))
        self.assertEqual(verify_schema(), "unverified")


if __name__ == "__main__":
    unittest.main()
