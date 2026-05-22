# Supabase Anon Key Deprecation — Impact Analysis

**Date:** 2026-03-06
**Source:** Supabase official notification email

---

## Source — Original Supabase Email

> We're reaching out because one or more of your Supabase projects have recently made requests to the Data API root endpoint (`/rest/v1/`) using the anon key.
>
> Starting **April 8th 2026**, we will be removing anon key access to this endpoint as part of an ongoing effort to tighten default security across Supabase.
>
> Normal Data API usage, i.e. querying tables via `/rest/v1/your_table` or via any Supabase client library is not affected.
>
> **Your projects that may be impacted:**
> - reporting (project-ref: `<redacted>`)
>
> Full details: https://github.com/orgs/supabase/discussions/42949

---

## Analysis — Codebase Audit

A full audit of the codebase was performed to determine whether any code relies on the deprecated endpoint.

### What is being removed

Anon key access to the bare `/rest/v1/` root endpoint — used by some clients to fetch the OpenAPI/PostgREST schema spec. Table-level queries (e.g. `/rest/v1/posts`) and all Supabase client library calls are unaffected.

### How this project connects to Supabase

The reporting pipeline interacts with the database through **direct PostgreSQL connections via `psycopg2`**:

```python
connection = psycopg2.connect(
    user=db_config.get("user"),
    password=db_config.get("password"),
    host=db_config.get("host"),
    port=db_config.get("port"),
    dbname=db_config.get("dbname")
)
```

Credentials are read from `.env`. No API key is involved.

### Findings

| Check | Result |
|---|---|
| Calls to `/rest/v1/` root endpoint | None found |
| Anon key used for API authentication | None found |
| Direct HTTP calls to any Supabase endpoint | None found |
| OpenAPI / schema spec fetching | None found |

**Notes on near-misses:**
- The word `anon` appears in SQL RLS policy names (e.g. `anon_select_all`) — this is a PostgreSQL role name, unrelated to the API anon key.
- `config/config_example.json` contains placeholder `supabase.url` and `supabase.key` fields.

---

## Conclusion

**As of this audit, the reporting pipeline was not affected by the April 8th 2026 deprecation** — it connects to Supabase exclusively via direct PostgreSQL (port 5432), bypassing the REST API entirely.

> **Update (post-audit):** the later `engagement/` pipeline *does* use the Supabase client library (`create_client` in `engagement/db/client.py`). The deprecation still does not apply — the client library calls table-level endpoints, not the `/rest/v1/` root — but note that key handling now matters: the engagement client prefers `service_role_key` and falls back to the anon/`key`.
