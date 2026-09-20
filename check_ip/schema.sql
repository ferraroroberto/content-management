-- check_ip schema — the illustration copyright-check store (issue #286).
-- Applied idempotently by check_ip.db.ensure_schema(); safe to re-run.
--
-- Replaces the three Excel workbooks the pipeline used to write
-- (metadata.xlsx / search_results_database.xlsx / api_history_database.xlsx).
-- SerpAPI response payloads deliberately do NOT live here: they are written
-- whole to sidecar files under <store>/api_raw and referenced by raw_path.
-- Excel capped a cell at 32,767 chars and silently truncated 47% of the
-- legacy payloads; a file has no such cap.

create table if not exists images (
    filename             text primary key,
    imgur_url            text,
    last_processed_date  text,
    total_links          integer not null default 0,
    exact_match_count    integer not null default 0,
    similar_match_count  integer not null default 0,
    linkedin_count       integer not null default 0,
    instagram_count      integer not null default 0,
    twitter_count        integer not null default 0,
    facebook_count       integer not null default 0,
    pinterest_count      integer not null default 0
);

create table if not exists results (
    id             integer primary key,
    local_image    text not null,
    uploaded_url   text,
    found_link     text not null,
    title          text,
    duplicate      integer not null default 0,   -- 0 unique · 1 secondary · 2 primary
    match_type     text,                         -- 'Exact Match' | 'Similar Match'
    source         text,                         -- LinkedIn | Twitter/X | Instagram | Facebook | Pinterest | NULL
    post_date      text,
    search_date    text,
    "order"        integer,

    -- ── Owner decision columns. AUTHORITATIVE. ────────────────────────────
    -- Only the owner writes these, via the control-panel tab. The screening
    -- skill is forbidden from touching them (check_ip.screen enforces it).
    ok             integer,   -- 0 = infringement, act on it · 1 = reviewed, acceptable
    person         text,      -- contact / thread reference
    chat           text,      -- outreach reference
    report         text,      -- report state or case reference
    fixed          integer,   -- 0/1 — taken down or corrected

    -- ── Screening columns. The skill writes ONLY these. ───────────────────
    -- The verdict answers one question: does the post credit the owner? A
    -- visible ROBERTOFERRARO.ART watermark is NOT credit on its own — the
    -- policy is that the post must mention him (name, tag, or link). The two
    -- flags record what makes an infringement worse, and are kept separate
    -- from the verdict so the backlog can be sliced by type later without a
    -- schema change. See db.severity() for how they rank.
    screen_verdict     text,    -- 'infringement' | 'acceptable' | 'unclear'
    screen_reason      text,
    screened_at        text,
    screen_source      text,    -- who/what produced the verdict
    poster_url         text,    -- profile of whoever posted it, for one-click follow-up
    screen_promotional integer, -- 1 = carries a self-promotional or commercial call to action
    screen_altered     integer, -- 1 = the image was edited: watermark cropped, painted over, removed

    -- Whoever posted it, derived from found_link (db.poster_key_for). Needed
    -- *before* screening so the queue can rank repeat offenders first, which
    -- poster_url cannot do — that is only known once a row has been screened.
    poster_key     text,

    -- The same link legitimately appears more than once for one image: the
    -- exact-match and visual-match searches both return it, and a later run
    -- re-finds it. Those are distinct findings the owner annotated separately
    -- (11 such groups carry conflicting ok values), so search_date is part of
    -- the identity. The pipeline still refuses to re-add a (link, match_type)
    -- it already holds for an image — that dedupe lives in process.build_rows,
    -- which is the right place for it. This constraint is the safety net.
    unique (local_image, found_link, match_type, search_date)
);

-- The screening queue filters on source + duplicate + screened_at, and the tab
-- filters by image; these two cover both without bloating the store.
create index if not exists results_queue_idx on results (source, duplicate, screened_at, ok);
create index if not exists results_image_idx on results (local_image);
-- The queue ranks by how many pending rows a poster holds, which is a grouped
-- count over this column on every `screen next` call.
create index if not exists results_poster_idx on results (poster_key);

create table if not exists api_history (
    id                  integer primary key,
    api_id              text unique,
    search_type         text,
    local_image         text,
    imgur_url           text,
    search_date         text,
    api_status          text,
    json_endpoint       text,
    created_at          text,
    processed_at        text,
    total_time_taken    real,
    engine              text,
    url                 text,
    search_engine_query text,
    playground_link     text,
    search_type_param   text,
    raw_path            text,                         -- sidecar file, relative to the store dir
    raw_truncated       integer not null default 0    -- 1 = hit Excel's 32,767-char cap, payload incomplete
);

create index if not exists api_history_image_idx on api_history (local_image, search_date);

-- Bookkeeping for migrate.py so a re-run is a no-op it can prove rather than assume.
create table if not exists meta (
    key   text primary key,
    value text
);
