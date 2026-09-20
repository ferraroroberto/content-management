"""SQLite store for the illustration copyright check (issue #286).

One file — ``results/check_ip/check_ip.db`` by default — holding the images,
every link a reverse-image search found, the owner's decisions on those links,
and the API-call log. ``results/*`` is gitignored, which matters: this is a
public repo and the store carries third-party profile URLs.

SerpAPI response payloads are *not* stored here. They go to sidecar files under
``<store>/api_raw`` and ``api_history.raw_path`` points at them. The Excel store
this replaces capped a cell at 32,767 characters and silently truncated 47% of
the payloads it held; a file has no such limit.

Everything that reads or writes the store goes through this module, so the
schema and the connection settings live in exactly one place.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.loader import load_full_config  # noqa: E402

logger = logging.getLogger("check_ip.db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Columns only the owner may write (via the control-panel tab). check_ip.screen
# refuses to touch these — the skill proposes, the owner decides.
OWNER_COLUMNS = ("ok", "person", "chat", "report", "fixed")

# Columns only the screening pass writes.
SCREEN_COLUMNS = ("screen_verdict", "screen_reason", "screened_at", "screen_source",
                  "poster_url", "screen_promotional", "screen_altered")

VERDICTS = ("infringement", "acceptable", "unclear")

# Columns added after the first release. ensure_schema() adds any that a store
# predating them is missing, because `create table if not exists` in schema.sql
# is a no-op once the table exists and would otherwise silently skip them.
ADDED_RESULT_COLUMNS = (
    ("screen_promotional", "integer"),
    ("screen_altered", "integer"),
    ("poster_key", "text"),
)

# Platform value meaning "no recognised platform" — the open web. Distinct from
# passing None, which means "every platform".
OPEN_WEB = "(open web)"


def source_clause(source: Optional[str], column: str = "source") -> tuple[str, list]:
    """Build the platform predicate shared by the queue and the review layer.

    ``OPEN_WEB`` has to become a NULL test rather than an equality one, which
    is easy to get subtly wrong in each caller — hence one helper.
    """
    if source is None:
        return "", []
    if source == OPEN_WEB:
        return f"{column} is null", []
    return f"{column} = ?", [source]


# ---------------------------------------------------------------------------
# poster identity and severity

# LinkedIn post URLs carry the poster in the path: /posts/<slug>_<headline>-
# activity-<id>-<hash>. Some carry no slug at all (/posts/activity-<id>-<hash>),
# which is why this returns None rather than inventing a key.
_POSTER_PATTERNS = (
    re.compile(r"linkedin\.com/posts/([^_/?#]+)_", re.I),
    re.compile(r"linkedin\.com/(?:in|company)/([^/?#]+)", re.I),
)


def poster_key_for(link: Optional[str]) -> Optional[str]:
    """Best-effort poster identity for ``link``, lowercased, or None.

    Used to rank repeat offenders ahead of one-offs. It is a ranking signal,
    not an identity claim: a miss costs nothing but ordering, so the patterns
    stay deliberately conservative rather than guessing at unusual URL shapes.
    """
    if not link:
        return None
    for pattern in _POSTER_PATTERNS:
        match = pattern.search(link)
        if match:
            slug = match.group(1).strip().lower()
            if slug and slug != "activity":
                return slug
    return None


def severity(verdict: Optional[str], promotional: object = None,
             altered: object = None) -> int:
    """Rank how much an infringement warrants acting on it: 0 low → 3 worst.

    0 is "nothing to act on" and covers both ``acceptable`` and ``unclear`` —
    an unresolved row is not a mild infringement, it is not yet a finding at
    all, and lumping it above acceptable would push guesses up the queue.
    Every aggravator adds one: no mention is 1, plus a self-promotional or
    commercial call to action, plus an edited image. 3 is all three together —
    the case the owner calls plain stealing.
    """
    if verdict != "infringement":
        return 0
    return 1 + (1 if promotional else 0) + (1 if altered else 0)


def severity_sql(alias: str = "r") -> str:
    """``severity()`` as an SQL expression, qualified with the results alias.

    Both callers join ``results`` against ``images``, so the columns are
    qualified rather than bare — an unqualified ``screen_verdict`` resolves
    today only because ``images`` happens not to have one. Sharing the
    expression keeps the tab's ordering and the queue's stats from drifting
    apart from the Python version above.
    """
    prefix = f"{alias}." if alias else ""
    return (
        f"case when {prefix}screen_verdict = 'infringement' "
        f"then 1 + coalesce({prefix}screen_promotional, 0) "
        f"+ coalesce({prefix}screen_altered, 0) else 0 end"
    )


# ---------------------------------------------------------------------------
# config


def config() -> dict:
    """Return the ``check_ip`` block of ``config.json``.

    Raises ``RuntimeError`` when the block is missing — the paths it carries
    have no sane default, so a clear error beats guessing.
    """
    block = load_full_config().get("check_ip")
    if not block:
        raise RuntimeError(
            "Missing 'check_ip' block in config/config.json — copy it from config_example.json"
        )
    return block


def _secret(cfg: dict, name: str, env: str) -> str:
    """Read one credential from the config block, falling back to the environment.

    A ``${VAR}`` placeholder (the shape the sibling repo's config used) means
    "not set here, look at the environment".
    """
    value = (cfg.get("api_keys") or {}).get(name) or ""
    if not value or (value.startswith("${") and value.endswith("}")):
        value = os.environ.get(env, "")
    return value


def credentials() -> dict:
    """The three API credentials, from config.json's api_keys or the environment.

    Loads the repo-root ``.env`` first so the keys can live there rather than
    in ``config.json`` — both are gitignored, but a ``.env`` keeps secrets out
    of the file that also carries ordinary settings. Never logged, never
    returned anywhere that renders.
    """
    try:
        from dotenv import load_dotenv  # noqa: PLC0415 — optional at import time

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:  # pragma: no cover — python-dotenv is in requirements
        logger.debug("python-dotenv unavailable; reading credentials from the environment only")

    cfg = config()
    return {
        "serpapi_key": _secret(cfg, "serpapi_key", "SERPAPI_KEY"),
        "imgur_client_id": _secret(cfg, "imgur_client_id", "IMGUR_CLIENT_ID"),
        "imgur_access_token": _secret(cfg, "imgur_access_token", "IMGUR_ACCESS_TOKEN"),
    }


def store_dir() -> Path:
    """Directory holding the database and the raw-payload sidecars."""
    folder = config().get("store_folder", "results/check_ip")
    path = Path(folder)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def db_path() -> Path:
    return store_dir() / "check_ip.db"


def raw_dir() -> Path:
    return store_dir() / "api_raw"


def raw_sidecar(api_id: str, *, truncated: bool = False) -> Path:
    """Path for one API payload, sharded by the id's first characters.

    Sharding keeps the directory from growing to tens of thousands of entries
    as runs accumulate. A truncated legacy payload gets a distinct suffix so
    nothing downstream tries to parse it as JSON.
    """
    safe = "".join(c for c in str(api_id) if c.isalnum() or c in "-_") or "unknown"
    suffix = ".json.truncated" if truncated else ".json"
    return raw_dir() / safe[:2] / f"{safe}{suffix}"


# ---------------------------------------------------------------------------
# connection


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the store, creating the directory and schema on first use."""
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    # WAL keeps the Streamlit tab readable while a run writes. The store is
    # repo-local by design — never put it on a synced folder, where the -wal
    # and -shm files sync independently of the database and corrupt it.
    conn.execute("pragma journal_mode = WAL")
    conn.execute("pragma synchronous = NORMAL")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply ``schema.sql``, then add any columns a pre-existing store lacks.

    ``schema.sql`` is all ``if not exists``, so on a store created before a
    column was introduced the ``create table`` is skipped entirely and the new
    column never appears. The ``alter table`` pass below closes that gap. It is
    additive only: nothing here drops, renames or rewrites a column, so no
    owner annotation can be lost by running it.

    The pass runs *before* the script, not after, because the script also
    creates an index over one of the added columns — on an existing store that
    index would be built against a column that does not exist yet and the whole
    call would fail. On a fresh store ``results`` is absent here, the pass is a
    no-op, and the script creates the table complete.
    """
    table_exists = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'results'"
    ).fetchone()
    if table_exists:
        existing = {r["name"] for r in conn.execute("pragma table_info(results)")}
        for column, decl in ADDED_RESULT_COLUMNS:
            if column not in existing:
                conn.execute(f"alter table results add column {column} {decl}")
                logger.info("➕ added results.%s", column)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# small helpers shared by migrate / run / screen / review


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "insert into meta (key, value) values (?, ?) "
        "on conflict(key) do update set value = excluded.value",
        (key, str(value)),
    )


def refresh_poster_keys(conn: sqlite3.Connection, *, only_missing: bool = True) -> int:
    """Populate ``results.poster_key`` from ``found_link``. Returns rows written.

    Rows imported before the column existed have it NULL, and the queue ranking
    needs it for every pending row, so the migration and the run both call this.
    Derived purely from ``found_link``, so re-running it cannot lose anything.
    """
    where = "found_link is not null"
    if only_missing:
        where += " and poster_key is null"
    rows = conn.execute(f"select id, found_link from results where {where}").fetchall()
    updates = [(poster_key_for(r["found_link"]), r["id"]) for r in rows]
    updates = [(key, row_id) for key, row_id in updates if key is not None]
    if updates:
        conn.executemany("update results set poster_key = ? where id = ?", updates)
        conn.commit()
    return len(updates)


def counts(conn: sqlite3.Connection) -> dict:
    """Row counts per table — the reconciliation the migration reports on."""
    out = {}
    for table in ("images", "results", "api_history"):
        out[table] = conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
    annotated = " or ".join(f"{c} is not null" for c in OWNER_COLUMNS)
    out["annotated"] = conn.execute(
        f"select count(*) as n from results where {annotated}"
    ).fetchone()["n"]
    out["screened"] = conn.execute(
        "select count(*) as n from results where screened_at is not null"
    ).fetchone()["n"]
    return out


def mark_duplicates(conn: sqlite3.Connection) -> dict:
    """Recompute the duplicate flag across the whole results table.

    0 = the URL appears once · 2 = oldest row for a URL seen several times
    (the primary) · 1 = every later row for that URL.

    One SQL pass with a window function, where the Excel version rebuilt and
    rewrote a 38 MB workbook on every run. Ordering matches the original:
    oldest search_date first, then post_date, then the result's position.
    """
    conn.execute(
        """
        with ranked as (
            select id,
                   count(*) over (partition by found_link) as n,
                   row_number() over (
                       partition by found_link
                       order by search_date, coalesce(post_date, '9999'), "order", id
                   ) as rn
            from results
        )
        update results
           set duplicate = (
                 select case when r.n = 1 then 0 when r.rn = 1 then 2 else 1 end
                   from ranked r where r.id = results.id
               )
        """
    )
    conn.commit()
    rows = conn.execute(
        "select duplicate, count(*) as n from results group by duplicate"
    ).fetchall()
    return {int(r["duplicate"]): r["n"] for r in rows}


def refresh_image_counts(conn: sqlite3.Connection) -> int:
    """Recompute the per-image link tallies from the results table."""
    conn.execute(
        """
        update images set
            total_links         = (select count(*) from results r where r.local_image = images.filename),
            exact_match_count   = (select count(*) from results r where r.local_image = images.filename and r.match_type = 'Exact Match'),
            similar_match_count = (select count(*) from results r where r.local_image = images.filename and r.match_type = 'Similar Match'),
            linkedin_count      = (select count(*) from results r where r.local_image = images.filename and r.source = 'LinkedIn'),
            instagram_count     = (select count(*) from results r where r.local_image = images.filename and r.source = 'Instagram'),
            twitter_count       = (select count(*) from results r where r.local_image = images.filename and r.source = 'Twitter/X'),
            facebook_count      = (select count(*) from results r where r.local_image = images.filename and r.source = 'Facebook'),
            pinterest_count     = (select count(*) from results r where r.local_image = images.filename and r.source = 'Pinterest')
        """
    )
    conn.commit()
    return conn.execute("select count(*) as n from images").fetchone()["n"]


def upsert_results(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Insert search results, leaving owner and screening columns untouched.

    Keyed on (local_image, found_link, match_type, search_date) — the same link
    reached by a different search, or re-found on a later date, is a separate
    finding the owner may annotate separately. Callers avoid re-adding a
    (link, match_type) they already hold via ``process.build_rows``; this
    conflict clause only catches an exact re-insert, and when it fires it
    updates the mechanical fields and preserves every owner and screening
    column on the row.
    """
    payload = [
        (
            r.get("local_image"), r.get("uploaded_url"), r.get("found_link"), r.get("title"),
            r.get("duplicate", 0), r.get("match_type"), r.get("source"),
            r.get("post_date"), r.get("search_date"), r.get("order"),
            poster_key_for(r.get("found_link")),
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        insert into results
            (local_image, uploaded_url, found_link, title, duplicate,
             match_type, source, post_date, search_date, "order", poster_key)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(local_image, found_link, match_type, search_date) do update set
            title       = excluded.title,
            source      = excluded.source,
            post_date   = excluded.post_date,
            "order"     = excluded."order",
            poster_key  = excluded.poster_key
        """,
        payload,
    )
    conn.commit()
    return len(payload)
