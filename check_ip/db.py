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
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import process  # noqa: E402 — pure helpers, no store access
from config.loader import load_full_config  # noqa: E402

logger = logging.getLogger("check_ip.db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Columns only the owner may write (via the control-panel tab). check_ip.screen
# refuses to touch these — the skill proposes, the owner decides.
OWNER_COLUMNS = ("ok", "person", "chat", "report", "fixed")

# The three CC BY-NC-ND conditions, in severity-reporting order. Each holds
# one of four states (issues #295, #305):
#
#     1    = met
#     0    = violated
#     2    = assessed, could not be established
#     NULL = never assessed
#
# #295 gave NULL its own meaning — never assessed, never a pass. #305 split the
# second meaning back out of it: a worker that looked at the post and could not
# establish a condition was storing the same NULL as a row nobody had ever
# opened, so the re-screen queue re-served rows that had just been judged and
# never drained. Two live batches were served the identical ten rows that way.
CONDITION_MET = 1
CONDITION_VIOLATED = 0
CONDITION_INDETERMINATE = 2
CONDITION_COLUMNS = ("screen_credit_ok", "screen_noncommercial_ok", "screen_unmodified_ok")

# Columns only the screening pass writes.
SCREEN_COLUMNS = ("screen_verdict", "screen_outcome", "screen_reason", "screened_at",
                  "screen_source", "poster_url") + CONDITION_COLUMNS

# What `screen_history` preserves when one of those writes replaces another
# (issue #301) — exactly SCREEN_COLUMNS, so a superseded opinion round-trips
# whole. Derived from that tuple rather than retyped: a screening column added
# later and not preserved here would be silently lost on the next re-screen.
SCREEN_HISTORY_COLUMNS = SCREEN_COLUMNS

VERDICTS = ("infringement", "acceptable", "unclear")

# The two kinds of ``unclear``, which live screening showed are unrelated.
# ``ambiguous`` is worth another look; ``nothing_to_assess`` never will be, so
# naming it is what lets the tab stop offering it.
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_NOTHING_TO_ASSESS = "nothing_to_assess"
UNCLEAR_OUTCOMES = (OUTCOME_AMBIGUOUS, OUTCOME_NOTHING_TO_ASSESS)

# Columns added after the first release. ensure_schema() adds any that a store
# predating them is missing, because `create table if not exists` in schema.sql
# is a no-op once the table exists and would otherwise silently skip them.
ADDED_RESULT_COLUMNS = (
    ("screen_promotional", "integer"),
    ("screen_altered", "integer"),
    ("poster_key", "text"),
    ("retired", "integer not null default 0"),
    ("screen_credit_ok", "integer"),
    ("screen_noncommercial_ok", "integer"),
    ("screen_unmodified_ok", "integer"),
    ("screen_outcome", "text"),
)

# Why any row currently carries ``retired = 1``. One reason exists, so it is a
# string rather than a column: `screen stats` and the tab both print it,
# which is what stops a fresh checkout being silently wrong about what it is
# skipping. A second reason is the moment to make it a column.
RETIRED_REASON = (
    f"{process.SIMILAR_MATCH}: a style lookalike, not a reuse — retired by issue #292"
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


# ---------------------------------------------------------------------------
# canonical link — the dedupe key, never what gets stored

# LinkedIn serves one post under a per-country host (`tn.`, `my.`, `rs.`, …),
# which is a different string for the same page, so the duplicate flag never
# fired and the queue screened the post once per mirror (issue #291). Every
# LinkedIn host in the store is either `www.` or a two-letter country code, so
# the pattern stays exactly that narrow — `business.`/`learning.`/`careers.`
# linkedin.com are different sites, not mirrors of one post.
_LOCALE_HOST = re.compile(r"^(?:[a-z]{2}\.)?linkedin\.com$", re.I)

# Query parameters that carry a locale or a referrer breadcrumb and nothing
# else. A denylist, not "drop the query" — that was measured against the live
# store first, where the query is what identifies the page on half the web:
# dropping it wholesale merged 3,913 distinct YouTube videos (`?v=`), 2,324
# distinct Facebook photos (`?fbid=`) and every stock-site search (`?k=`) into
# one row each. `?lang=` on X is the mirror that matters here, and it is the
# same locale dimension as LinkedIn's host.
_LOCALE_PARAMS = frozenset({
    "lang", "locale", "hl", "tl",              # locale selectors
    "trk", "trackingid", "originalsubdomain",  # LinkedIn referrer breadcrumbs
    "rcm", "fbclid", "gclid", "igshid", "si", "src", "ref_src", "ref_url",
})


def _is_locale_param(segment: str) -> bool:
    name = segment.split("=", 1)[0].strip().lower()
    return name in _LOCALE_PARAMS or name.startswith("utm_")


def canonical_link_for(link: Optional[str]) -> Optional[str]:
    """The dedupe key for ``link``: mirrors of one page fold onto one string.

    Lowercases the host and path, folds a LinkedIn locale host onto `www.`,
    drops the fragment, a trailing slash and the locale/tracking query
    parameters above, and sorts whatever query is left so parameter order
    cannot split a group.

    **Never stored.** ``found_link`` keeps the URL that was actually found and
    opened; this is only what ``mark_duplicates`` partitions on. Returns None
    for a blank link, matching ``poster_key_for``.
    """
    if not link or not str(link).strip():
        return None
    raw = str(link).strip()
    parts = urlsplit(raw)
    if not parts.netloc:
        # Not an absolute URL — nothing to normalise beyond case and the slash.
        return raw.lower().rstrip("/") or None
    host = parts.netloc.lower()
    if _LOCALE_HOST.match(host):
        host = "www.linkedin.com"
    path = parts.path.rstrip("/").lower()
    kept = sorted(seg for seg in parts.query.split("&") if seg and not _is_locale_param(seg))
    query = f"?{'&'.join(kept)}" if kept else ""
    return f"{parts.scheme.lower()}://{host}{path}{query}"


def condition_state(value: object) -> Optional[int]:
    """Normalise one licence condition to ``1`` met, ``0`` violated, ``2``
    assessed-but-indeterminate, or ``None`` never assessed.

    Accepts the shapes a caller realistically holds — ``True``/``False``, an
    ``int``, a ``"met"``/``"violated"``/``"unknown"`` word, or ``None`` — and
    rejects anything else rather than guessing. A rejected value must not fall
    through as "not assessed": that would turn a typo into a silent NULL and
    NULL is the state this whole model exists to keep honest.

    ``"unknown"`` is the word a worker uses when it looked and could not tell,
    so it maps to ``2`` — an answer — and **not** to ``None`` (issue #305).
    ``None`` is reachable only by passing ``None`` or by naming the absence of
    an answer outright (``"unassessed"``, ``""``), which is what a caller that
    simply omitted the condition does.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return CONDITION_MET if value else CONDITION_VIOLATED
    if isinstance(value, int):
        if value in (CONDITION_VIOLATED, CONDITION_MET, CONDITION_INDETERMINATE):
            return value
        raise ValueError(f"licence condition must be 0, 1, 2 or None, got {value!r}")
    text = str(value).strip().lower()
    if text in ("met", "ok", "1", "true", "yes"):
        return CONDITION_MET
    if text in ("violated", "broken", "0", "false", "no"):
        return CONDITION_VIOLATED
    if text in ("unknown", "indeterminate", "could not establish", "2"):
        return CONDITION_INDETERMINATE
    if text in ("unassessed", "not assessed", "none", ""):
        return None
    raise ValueError(f"licence condition must be 0, 1, 2 or None, got {value!r}")


def severity(credit_ok: object = None, noncommercial_ok: object = None,
             unmodified_ok: object = None) -> int:
    """How many of the three licence conditions were found violated: 0–3.

    The count is of conditions **positively established as broken**. Neither
    an unassessed condition (``None``) nor an indeterminate one (``2``) adds
    anything — neither is evidence of a violation — but neither is a pass
    either, which is why a severity of 0 is never on its own a statement that
    a post is compliant. ``all_conditions_met()`` is the other half of that
    sentence and the tab prints both. Severity is therefore untouched by issue
    #305: no existing row's severity moves.

    3 is no credit **and** commercial use **and** a modified image: the case
    the owner calls plain stealing.
    """
    return sum(1 for value in (credit_ok, noncommercial_ok, unmodified_ok)
               if condition_state(value) == 0)


def severity_sql(alias: str = "r") -> str:
    """``severity()`` as an SQL expression, qualified with the results alias.

    Callers join ``results`` against ``images``, so the columns are qualified
    rather than bare. Sharing the expression keeps the tab's ordering and the
    queue's stats from drifting apart from the Python version above; a test
    compares the two across all 27 combinations.

    ``col = 0`` is NULL for an unassessed condition and a NULL ``case``
    predicate takes the ``else`` branch, so a NULL adds 0 — deliberately, and
    without a ``coalesce`` that would make it indistinguishable from a pass.
    An indeterminate ``2`` is simply not ``0`` and adds 0 the same way.
    """
    prefix = f"{alias}." if alias else ""
    return " + ".join(
        f"case when {prefix}{column} = 0 then 1 else 0 end" for column in CONDITION_COLUMNS
    )


def fully_assessed(credit_ok: object = None, noncommercial_ok: object = None,
                   unmodified_ok: object = None) -> bool:
    """Whether all three conditions were actually looked at.

    The question the old model could not ask. A row can be severity 0 and not
    assessed at all, and the difference between "checked, nothing wrong" and
    "never checked" is the whole point of issue #295.

    "Looked at" is the test, not "settled": an indeterminate ``2`` counts here,
    because a worker did open the post and answer. That is what lets the
    re-screen queue drain (issue #305) — the queue asks *has anyone looked*,
    while ``all_conditions_met()`` below asks *did it pass*.
    """
    return all(condition_state(value) is not None
               for value in (credit_ok, noncommercial_ok, unmodified_ok))


def assessed_sql(alias: str = "r") -> str:
    """``fully_assessed()`` as an SQL predicate."""
    prefix = f"{alias}." if alias else ""
    return " and ".join(f"{prefix}{column} is not null" for column in CONDITION_COLUMNS)


def all_conditions_met(credit_ok: object = None, noncommercial_ok: object = None,
                       unmodified_ok: object = None) -> bool:
    """Whether all three conditions were positively established as met.

    The compliance test, kept apart from ``fully_assessed()`` on purpose. Only
    a ``1`` counts: a ``2`` is an answer, but the answer is "could not tell",
    and NULL is not an answer at all. Neither may ever read as a pass.
    """
    return all(condition_state(value) == CONDITION_MET
               for value in (credit_ok, noncommercial_ok, unmodified_ok))


def met_sql(alias: str = "r") -> str:
    """``all_conditions_met()`` as an SQL predicate.

    ``col = 1`` is NULL for an unassessed condition and false for a ``2``, and
    an ``and`` chain with a NULL in it is never true — so neither state can
    reach the ``acceptable`` branch of ``verdict_sql`` or the tab's compliant
    filter. Rendered here once rather than hand-written at each call site,
    which is how ``assessed_sql`` came to be standing in for it.
    """
    prefix = f"{alias}." if alias else ""
    return " and ".join(f"{prefix}{column} = {CONDITION_MET}" for column in CONDITION_COLUMNS)


def indeterminate(credit_ok: object = None, noncommercial_ok: object = None,
                  unmodified_ok: object = None) -> bool:
    """Whether every condition was answered and at least one came back ``2``.

    Its own counter (issue #305) because it wants a different action from a
    NULL: NULL wants another screening pass, while this row's screening pass is
    done and what is left is the owner's own judgement, or nothing. Deliberately
    false while any condition is still NULL, so the two counters never
    double-count a row.
    """
    conditions = (credit_ok, noncommercial_ok, unmodified_ok)
    return (fully_assessed(*conditions)
            and any(condition_state(value) == CONDITION_INDETERMINATE for value in conditions))


def indeterminate_sql(alias: str = "r") -> str:
    """``indeterminate()`` as an SQL predicate."""
    prefix = f"{alias}." if alias else ""
    any_two = " or ".join(f"{prefix}{column} = {CONDITION_INDETERMINATE}"
                          for column in CONDITION_COLUMNS)
    return f"({assessed_sql(alias)}) and ({any_two})"


def verdict_for(credit_ok: object = None, noncommercial_ok: object = None,
                unmodified_ok: object = None) -> str:
    """The verdict the three conditions imply. ``screen_verdict`` is derived.

    ``infringement`` as soon as one condition is violated, ``acceptable`` only
    once all three are *met*, and ``unclear`` otherwise — which is why a post
    that credits the owner but was never checked for commercial use reads as
    unclear rather than acceptable.

    A row whose conditions were all answered but where one came back
    indeterminate stays ``unclear`` too (issue #305): nothing was found wrong,
    and nothing was established either, so it leaves the re-screen queue
    without ever claiming to be compliant.
    """
    conditions = (credit_ok, noncommercial_ok, unmodified_ok)
    if severity(*conditions):
        return "infringement"
    return "acceptable" if all_conditions_met(*conditions) else "unclear"


def verdict_sql(alias: str = "r") -> str:
    """``verdict_for()`` as an SQL expression, composed of the two above."""
    return (f"case when ({severity_sql(alias)}) > 0 then 'infringement' "
            f"when ({met_sql(alias)}) then 'acceptable' else 'unclear' end")


def permanent_sql(alias: str = "r") -> str:
    """Predicate for a row no further look can ever assess.

    One rendering shared by the tab's filters, its header and the queue stats —
    they must agree on what gets excluded from the re-screen pile, and three
    hand-written copies of a ``coalesce`` is how they would stop agreeing.
    """
    prefix = f"{alias}." if alias else ""
    return f"coalesce({prefix}screen_outcome, '') = '{OUTCOME_NOTHING_TO_ASSESS}'"


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


def raw_sidecar(api_id: str) -> Path:
    """Path for one API payload, sharded by the id's first characters.

    Sharding keeps the directory from growing to tens of thousands of entries
    as runs accumulate.
    """
    safe = "".join(c for c in str(api_id) if c.isalnum() or c in "-_") or "unknown"
    return raw_dir() / safe[:2] / f"{safe}.json"


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
    needs it for every pending row, so the run calls this.
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
    """Row counts per table — the before/after check the migration lane reports on."""
    out = {}
    for table in ("images", "results", "api_history", "screen_history"):
        out[table] = conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
    annotated = " or ".join(f"{c} is not null" for c in OWNER_COLUMNS)
    out["annotated"] = conn.execute(
        f"select count(*) as n from results where {annotated}"
    ).fetchone()["n"]
    out["screened"] = conn.execute(
        "select count(*) as n from results where screened_at is not null"
    ).fetchone()["n"]
    out["retired"] = conn.execute(
        "select count(*) as n from results where retired = 1"
    ).fetchone()["n"]
    return out


def mark_duplicates(conn: sqlite3.Connection) -> dict:
    """Recompute the duplicate flag across the whole results table.

    0 = the page appears once · 2 = the primary row for a page seen several
    times · 1 = every other row for that page.

    Rows group by ``canonical_link_for(found_link)``, not by the raw string
    (issue #291): LinkedIn's locale hosts and X's ``?lang=`` are mirrors of one
    post, and partitioning on the raw URL left every mirror ``duplicate = 0``,
    so the queue served the same post once per host.

    The primary is **elected**, not simply the oldest, because widening the
    group is what makes the choice load-bearing — a mirror taking the primary
    slot from the row that carries the work would send an already-judged post
    back through the queue as a different row while the verdict sat on a row
    nobody serves. In order: a live row before a retired one (a retired mirror
    must not hide a servable row), a row the owner annotated before one he has
    not (an annotated secondary would drop off the tab), a screened row before
    an unscreened one, and only then the original ordering — oldest
    search_date, then post_date, then the result's position.

    Still one SQL pass with a window function, where the Excel version rebuilt
    and rewrote a 38 MB workbook on every run.
    """
    conn.create_function("canonical_link", 1, canonical_link_for, deterministic=True)
    annotated = " or ".join(f"{c} is not null" for c in OWNER_COLUMNS)
    conn.execute(
        f"""
        with keyed as (
            select id, retired, screened_at, search_date, post_date, "order",
                   canonical_link(found_link) as key,
                   case when ({annotated}) then 0 else 1 end as unjudged
              from results
        ),
        ranked as (
            select id,
                   count(*) over (partition by key) as n,
                   row_number() over (
                       partition by key
                       order by retired,
                                unjudged,
                                case when screened_at is null then 1 else 0 end,
                                search_date, coalesce(post_date, '9999'), "order", id
                   ) as rn
              from keyed
        )
        update results
           set duplicate = case when r.n = 1 then 0 when r.rn = 1 then 2 else 1 end
          from ranked r
         where r.id = results.id
        """
    )
    conn.commit()
    rows = conn.execute(
        "select duplicate, count(*) as n from results group by duplicate"
    ).fetchall()
    return {int(r["duplicate"]): r["n"] for r in rows}


def push_screen_history(conn: sqlite3.Connection, row_id: int) -> bool:
    """Ensure row ``row_id``'s current screening opinion is in ``screen_history``.

    Returns True when this row carried an opinion and that opinion is now
    confirmed preserved; False when the row has never been screened, where
    there is no prior observation to keep and only an empty one to invent.
    Raises ``RuntimeError`` when a prior opinion exists but preservation could
    **not** be confirmed — the caller is about to overwrite the only other copy
    of it, so "not confirmed" must abort the transaction rather than pass as
    success.

    **Deliberately does not commit.** The caller owns the transaction, because
    the whole point of this function is that the copy and the overwrite that
    supersedes it land together or not at all (issue #301). Committing here
    would create exactly the window it exists to close.

    ``insert or ignore`` resolves against ``unique (result_id, screened_at)``:
    re-preserving a state already in the table is a no-op rather than a
    duplicate. That is what stops a re-screen recording an opinion the table
    already holds a second time.
    The only constraint on the table is that key, so nothing else is masked —
    and "already there" is just as good an answer as "written now", which is
    why the check below asks whether the opinion is *preserved* rather than
    whether this call happened to be the one that wrote it.

    No owner column appears in either statement, or in the table they write to.
    """
    prior = conn.execute(
        "select screened_at from results where id = ? and screened_at is not null",
        (row_id,),
    ).fetchone()
    if prior is None:
        return False

    columns = ", ".join(SCREEN_HISTORY_COLUMNS)
    conn.execute(
        f"""
        insert or ignore into screen_history (result_id, {columns}, recorded_at)
        select id, {columns}, ?
          from results
         where id = ?
        """,
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row_id),
    )
    kept = conn.execute(
        "select 1 from screen_history where result_id = ? and screened_at = ?",
        (row_id, prior["screened_at"]),
    ).fetchone()
    if kept is None:
        raise RuntimeError(
            f"refusing to overwrite row {row_id}: its previous screening opinion "
            f"could not be preserved in screen_history"
        )
    return True


def refresh_image_counts(conn: sqlite3.Connection) -> int:
    """Recompute the per-image link tallies from the results table.

    Counts every stored row, retired ones included — these tallies are the
    search history of an illustration, not the screening backlog, and
    ``similar_match_count`` would otherwise read 0 for images that really did
    return similar matches before they were retired.
    """
    conn.execute(
        f"""
        update images set
            total_links         = (select count(*) from results r where r.local_image = images.filename),
            exact_match_count   = (select count(*) from results r where r.local_image = images.filename and r.match_type = '{process.EXACT_MATCH}'),
            similar_match_count = (select count(*) from results r where r.local_image = images.filename and r.match_type = '{process.SIMILAR_MATCH}'),
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
