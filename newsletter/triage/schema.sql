-- newsletter triage store — Supabase / Postgres (same project DB as reporting + engagement).
-- Apply once via the Supabase dashboard SQL editor (PostgREST cannot run DDL).
-- Idempotent: safe to re-run. `newsletter/triage/db.py::ensure_schema()` probes triage_runs
-- and fails loud with a pointer to this file when the tables are missing.
--
-- Access is through the service_role key (RLS bypass), like the engagement tables.

-- one engine run = one window (closed Saturday→Saturday week) of one kind.
-- Re-running a stored window deletes the row (children cascade) and inserts a fresh one.
create table if not exists triage_runs (
    id                  bigserial   primary key,
    window_start        date        not null,                 -- inclusive (Gmail after:)
    window_end          date        not null,                 -- exclusive (Gmail before:)
    kind                text        not null default 'live',  -- live | backtest
    edition             text,                                 -- backtest: the replayed edition (N226)
    status              text        not null default 'running',  -- running | done | failed
    started_at          timestamptz not null default now(),
    finished_at         timestamptz,
    source              text,                                 -- gmail | cache
    model               text,                                 -- hub alias used for scoring
    criteria_version    text,
    report_path         text,                                 -- markdown rendered in the control panel
    stats               jsonb       default '{}'::jsonb,
    unique (window_start, window_end, kind)
);

create table if not exists triage_emails (
    run_id              bigint      not null references triage_runs(id) on delete cascade,
    message_id          text        not null,
    sender_name         text,
    sender_address      text,
    subject             text,
    ts                  timestamptz,
    sender_basis        text,                                 -- prior used: override/ranked/floor/new
    is_new              boolean     default false,
    primary key (run_id, message_id)
);

create table if not exists triage_candidates (
    run_id              bigint      not null references triage_runs(id) on delete cascade,
    cid                 text        not null,                 -- <message_id>:<n>
    message_id          text,
    url                 text,
    canonical           text,
    domain              text,
    title               text,
    author              text,
    kind                text,                                 -- article | video | pdf | …
    topic               text,
    score               double precision,
    verdict             text,                                 -- selected | runner-up | candidate | vetoed | duplicate | low | unknown
    reason              text,
    summary             text,
    sender_weight       double precision,
    sender_basis        text,
    is_new_sender       boolean     default false,
    paywalled           boolean,
    in_notion           boolean     default false,
    fetched_ok          boolean,
    meta                jsonb,                                -- stage-A metadata score
    content             jsonb,                                -- stage-B content score
    suggested           text,                                 -- pick | runner | null
    suggested_rank      integer,
    suggested_star      boolean     default false,
    suggested_must_read boolean     default false,
    primary key (run_id, cid)
);
create index if not exists triage_candidates_suggested_idx on triage_candidates (run_id, suggested);

-- the owner's decisions — keyed by window + canonical URL, NOT by run, so a re-run
-- of the week keeps the earlier ticks. Single source for the sender-tier tally.
create table if not exists triage_decisions (
    window_start        date        not null,
    window_end          date        not null,
    canonical           text        not null,
    cid                 text,
    sender_address      text,
    title               text,
    url                 text,
    topic               text,
    pick                boolean     not null default false,
    star                boolean     default false,
    must_read           boolean     default false,
    note                text,
    decided_at          timestamptz not null default now(),
    primary key (window_start, window_end, canonical)
);
create index if not exists triage_decisions_sender_idx on triage_decisions (sender_address);

-- the review comment ("why these choices") per window — survives re-runs.
create table if not exists triage_reviews (
    window_start        date        not null,
    window_end          date        not null,
    comment             text,
    reviewed_at         timestamptz not null default now(),
    n_pick              integer,
    n_star              integer,
    tier_changes        jsonb,
    primary key (window_start, window_end)
);

-- criteria notes distilled from a review (LLM-proposed, owner-accepted).
-- Accepted rows are exported to the tracked newsletter/triage/lessons.json.
create table if not exists triage_lessons (
    id                  bigserial   primary key,
    run_id              bigint      references triage_runs(id) on delete set null,
    window_start        date,
    window_end          date,
    text                text        not null,
    model               text,
    proposed_at         timestamptz not null default now(),
    accepted            boolean     not null default false,
    accepted_at         timestamptz
);

-- 54-week knowledge base imported from results/newsletter/triage/history/.
create table if not exists triage_editions (
    number              text        primary key,              -- N226
    date                date,
    title               text,
    must_read_title     text,
    substack_url        text,
    n_leader            integer,
    n_innov             integer,
    n_persdev           integer
);

create table if not exists triage_picks (
    article_id          text        primary key,              -- Notion page id
    edition             text        references triage_editions(number) on delete cascade,
    title               text,
    url                 text,
    canonical           text,
    domain              text,
    topic               text,
    author              text,
    star                boolean     default false,
    must_read           boolean     default false,
    created             date,
    summary             text
);
create index if not exists triage_picks_edition_idx on triage_picks (edition);
