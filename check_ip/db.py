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
# 1 = met · 0 = violated · NULL = not assessed (issue #295).
CONDITION_COLUMNS = ("screen_credit_ok", "screen_noncommercial_ok", "screen_unmodified_ok")

# Columns only the screening pass writes.
SCREEN_COLUMNS = ("screen_verdict", "screen_outcome", "screen_reason", "screened_at",
                  "screen_source", "poster_url") + CONDITION_COLUMNS

# Superseded by CONDITION_COLUMNS and no longer written by anything (#295).
# Named rather than deleted so the migration that mapped them stays auditable.
FROZEN_SCREEN_COLUMNS = ("screen_promotional", "screen_altered")

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
# string rather than a column: `screen stats` and the migration both print it,
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
    """Normalise one licence condition to ``1`` met, ``0`` violated, ``None``.

    Accepts the shapes a caller realistically holds — ``True``/``False``, an
    ``int``, a ``"met"``/``"violated"``/``"unknown"`` word, or ``None`` — and
    rejects anything else rather than guessing. A rejected value must not fall
    through as "not assessed": that would turn a typo into a silent NULL and
    NULL is the state this whole model exists to keep honest.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        if value in (0, 1):
            return value
        raise ValueError(f"licence condition must be 0, 1 or None, got {value!r}")
    text = str(value).strip().lower()
    if text in ("met", "ok", "1", "true", "yes"):
        return 1
    if text in ("violated", "broken", "0", "false", "no"):
        return 0
    if text in ("unknown", "unassessed", "not assessed", "none", ""):
        return None
    raise ValueError(f"licence condition must be 0, 1 or None, got {value!r}")


def severity(credit_ok: object = None, noncommercial_ok: object = None,
             unmodified_ok: object = None) -> int:
    """How many of the three licence conditions were found violated: 0–3.

    The count is of conditions **positively established as broken**. An
    unassessed condition (``None``) adds nothing — it is not evidence of a
    violation — but it is not a pass either, which is why a severity of 0 is
    never on its own a statement that a post is compliant. ``fully_assessed()``
    is the other half of that sentence and the tab prints both.

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
    """
    return all(condition_state(value) is not None
               for value in (credit_ok, noncommercial_ok, unmodified_ok))


def assessed_sql(alias: str = "r") -> str:
    """``fully_assessed()`` as an SQL predicate."""
    prefix = f"{alias}." if alias else ""
    return " and ".join(f"{prefix}{column} is not null" for column in CONDITION_COLUMNS)


def verdict_for(credit_ok: object = None, noncommercial_ok: object = None,
                unmodified_ok: object = None) -> str:
    """The verdict the three conditions imply. ``screen_verdict`` is derived.

    ``infringement`` as soon as one condition is violated, ``acceptable`` only
    once all three are met, and ``unclear`` while any is still unassessed —
    which is why a post that credits the owner but was never checked for
    commercial use reads as unclear rather than acceptable.
    """
    conditions = (credit_ok, noncommercial_ok, unmodified_ok)
    if severity(*conditions):
        return "infringement"
    return "acceptable" if fully_assessed(*conditions) else "unclear"


def verdict_sql(alias: str = "r") -> str:
    """``verdict_for()`` as an SQL expression, composed of the two above."""
    return (f"case when ({severity_sql(alias)}) > 0 then 'infringement' "
            f"when {assessed_sql(alias)} then 'acceptable' else 'unclear' end")


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


def retire_similar_matches(conn: sqlite3.Connection) -> dict:
    """Flag every stored ``Similar Match`` row as out of scope. Never deletes.

    Retirement is a one-way door only in intent: the rows, their titles, their
    dates and their screening verdicts all stay exactly where they are, and
    clearing the flag brings them back. That matters because each one cost a
    SerpAPI call and no amount of code could re-derive it for free.

    A row carrying **any** owner annotation is left alone. There are none today
    — ten months of manual triage produced zero decisions on a similar match,
    which is most of why the category is being retired — but a decision the
    owner made is a decision that stays visible in the tab, and skipping those
    rows makes that structurally true rather than merely true-by-count.

    Returns the counts: ``retired`` newly flagged, ``already_retired``,
    ``kept_annotated`` left visible.
    """
    annotated = " or ".join(f"{c} is not null" for c in OWNER_COLUMNS)
    row = conn.execute(
        f"""
        select
            sum(case when retired = 0 and not ({annotated}) then 1 else 0 end) as to_retire,
            sum(case when retired = 1 then 1 else 0 end)                       as already_retired,
            sum(case when retired = 0 and ({annotated}) then 1 else 0 end)     as kept_annotated
          from results
         where match_type = ?
        """,
        (process.SIMILAR_MATCH,),
    ).fetchone()
    counts = {
        "retired": row["to_retire"] or 0,
        "already_retired": row["already_retired"] or 0,
        "kept_annotated": row["kept_annotated"] or 0,
    }
    if counts["retired"]:
        conn.execute(
            f"update results set retired = 1 "
            f"where match_type = ? and retired = 0 and not ({annotated})",
            (process.SIMILAR_MATCH,),
        )
        conn.commit()
    return counts


def backfill_licence_conditions(conn: sqlite3.Connection) -> dict:
    """Map rows screened under the credit-only question onto the three
    licence conditions (issue #295). Writes screening columns only.

    The mapping invents nothing. ``screen_credit_ok`` is filled for every
    screened row because credit *was* the question that pass asked; the other
    two are filled only where the old flag positively fired, and left NULL
    otherwise. A row that was not flagged promotional was never checked for a
    paid or business context, and a row whose watermark was intact was never
    checked for a crop, added text or a translation — recording either as met
    would invent a fact the screening never established.

    ``screen_verdict`` is then recomputed from the conditions, because it is
    derived now. The visible consequence is deliberate: an ``acceptable`` row
    becomes ``unclear`` carrying ``screen_credit_ok = 1``, which reads as
    "credit met, the other two unknown" rather than "compliant".

    A legacy ``unclear`` row establishes nothing, so all three conditions stay
    NULL — which means the conditions alone cannot say whether a row has been
    through here. ``screen_outcome`` is what marks it: those rows are set to
    ``ambiguous``, the state that claims the least. The legacy bucket mixes
    "could not tell" with "nothing to assess" and there is no way to tell which
    from the store, so the mapping picks the one that keeps the row in the
    re-screen pile rather than the one that quietly retires it.

    Only rows that are screened, carry all three conditions NULL **and** have
    no outcome are touched, so a re-run is a no-op and a row screened under the
    new criteria is never overwritten. Nothing is deleted and no owner column
    is in any statement here.
    """
    untouched = (" and ".join(f"{c} is null" for c in CONDITION_COLUMNS)
                 + " and screen_outcome is null")
    scope = f"screened_at is not null and {untouched}"

    before = conn.execute(
        """
        select screen_verdict as verdict, count(*) as n
          from results where screened_at is not null group by screen_verdict
        """
    ).fetchall()
    to_map = conn.execute(f"select count(*) from results where {scope}").fetchone()[0]
    already_mapped = conn.execute(
        f"select count(*) from results where screened_at is not null and not ({untouched})"
    ).fetchone()[0]

    conn.execute(
        f"""
        update results
           set screen_credit_ok = case screen_verdict
                                      when 'infringement' then 0
                                      when 'acceptable'   then 1
                                      else null end,
               screen_noncommercial_ok = case when screen_promotional = 1 then 0 else null end,
               screen_unmodified_ok    = case when screen_altered = 1 then 0 else null end
         where {scope}
        """
    )
    # Second statement, not a second expression in the first: it reads the
    # conditions the statement above just wrote, and SQLite evaluates an
    # UPDATE's SET list against the pre-update row. Applied to every screened
    # row, which is a no-op on rows screened under the new criteria —
    # record_verdict already refuses to store a verdict the conditions deny,
    # and the coalesce keeps an outcome such a row already chose for itself.
    conn.execute(
        f"""
        update results
           set screen_verdict = ({verdict_sql('')}),
               screen_outcome = case when ({verdict_sql('')}) = 'unclear'
                                     then coalesce(screen_outcome, '{OUTCOME_AMBIGUOUS}')
                                     else null end
         where screened_at is not null
        """
    )
    conn.commit()

    after = conn.execute(
        """
        select screen_verdict as verdict, count(*) as n
          from results where screened_at is not null group by screen_verdict
        """
    ).fetchall()
    per_condition = {}
    for column in CONDITION_COLUMNS:
        row = conn.execute(
            f"""
            select sum(case when {column} = 1 then 1 else 0 end) as met,
                   sum(case when {column} = 0 then 1 else 0 end) as violated,
                   sum(case when {column} is null then 1 else 0 end) as not_assessed
              from results where screened_at is not null
            """
        ).fetchone()
        per_condition[column] = {k: (row[k] or 0) for k in ("met", "violated", "not_assessed")}

    return {
        "mapped": to_map,
        "already_mapped": already_mapped,
        "verdicts_before": {r["verdict"]: r["n"] for r in before},
        "verdicts_after": {r["verdict"]: r["n"] for r in after},
        "conditions": per_condition,
        "not_fully_assessed": conn.execute(
            f"""select count(*) from results
                 where screened_at is not null and not ({assessed_sql('')})"""
        ).fetchone()[0],
    }


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
