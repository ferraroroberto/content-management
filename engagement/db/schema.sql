-- engagement pipeline schema — Phase 1 MVP
-- Apply once via the Supabase dashboard SQL editor.
-- Idempotent: safe to re-run.

create table if not exists commenters (
    platform            text        not null,
    account_url         text        not null,
    display_name        text,
    reputation_score    double precision default 0,
    classification      text        default 'unknown',     -- whitelist / blacklist / unknown
    signals             jsonb       default '{}'::jsonb,   -- rolling features (cadence, lengths, ...)
    counters            jsonb       default '{}'::jsonb,   -- comments_seen, posts_seen, generic_praise_hits, ...
    notes               text,                              -- free-text from manual review
    first_seen          timestamptz default now(),
    last_seen           timestamptz default now(),
    primary key (platform, account_url)
);

create table if not exists comments (
    platform            text        not null,
    comment_id          text        not null,             -- LI URN or platform-native id
    post_url            text        not null,
    commenter_url       text        not null,
    display_name        text,
    text                text,
    posted_at           timestamptz,
    scraped_at          timestamptz default now(),
    classification      text        default 'unknown',    -- human / ai / unknown
    confidence          double precision,
    verdict_source      text,                             -- rules / local / llm
    verdict_reasons     jsonb,                            -- which rules fired, with weights
    suggested_action    text,                             -- surface_to_me / like_and_thanks / ignore
    suggested_reply     text,
    status              text        default 'pending',    -- pending / approved / sent / rejected / ignored
    decided_at          timestamptz,
    my_reply_text       text,                             -- if I (post author) already replied — captured at scrape time
    my_replied_at       timestamptz,                      -- approximate from LI relative timestamp
    primary key (platform, comment_id)
);

-- Idempotent column-adds for anyone who applied an earlier schema revision.
alter table comments add column if not exists my_reply_text text;
alter table comments add column if not exists my_replied_at timestamptz;

create index if not exists comments_status_idx     on comments (status, classification);
create index if not exists comments_commenter_idx  on comments (platform, commenter_url);
create index if not exists comments_scraped_idx    on comments (scraped_at desc);
