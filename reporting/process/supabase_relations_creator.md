## Supabase Relations Creator

### Purpose
Create and populate a relational structure in Supabase from Notion database relations. It derives and builds junction tables, a master catalog table, and bulk-loads relation rows from JSONB data stored in source tables.

### Inputs
- Notion metadata files (relative to `reporting/`):
  - `notion/notion_database_list.json`
  - `notion/notion_database_relations.json`
- Environment variables (loaded via `.env`) per environment suffix: `db_user_{environment}`, `db_password_{environment}`, `db_host_{environment}`, `db_port_{environment}`, `db_name_{environment}` where `{environment}` is `local` or `cloud`.
- Existing source tables per Notion database with columns:
  - `notion_id` (text)
  - `notion_data_jsonb` (jsonb)

### High-level Flow
1. Load DB config and connect (autocommit on).
2. Load Notion database list and relation definitions from JSON.
3. Build a mapping: Notion database ID → `{ name, supabase_table, replication }`.
4. Drop existing catalog table and all junction tables inferred from current relations.
5. Create catalog table `notion_relations_master` and apply RLS policies.
6. Populate the catalog with one record per relation edge, including resolved table names and computed junction table name.
7. Create junction tables (naming rules below) with appropriate columns, unique constraints, and indexes; apply RLS policies to each.
8. Extract relations from `notion_data_jsonb` via set-based SQL and insert into junction tables with `ON CONFLICT DO NOTHING`.

### Table Naming Rules
- Self-relation (origin table equals related table):
  - Junction: `{table}_relations`
- Different tables:
  - If deduplicate mode is ON (single table per pair): `{min(origin, related)}_to_{max(origin, related)}`
  - If deduplicate mode is OFF (directional): `{origin}_to_{related}` and `{related}_to_{origin}`

### Catalog Table: `notion_relations_master`
Columns:
- `id` SERIAL PK
- `origin_database_id`, `origin_database_name`, `origin_supabase_table`
- `relation_field_name`
- `related_database_id`, `related_database_name`, `related_supabase_table`
- `junction_table_name`
- `created_at` TIMESTAMP DEFAULT now()

Indexes:
- `(origin_supabase_table)`, `(related_supabase_table)`, `(junction_table_name)`

RLS policies (applied after creation):
- Enable RLS
- Policies for role `anon` on SELECT, INSERT, UPDATE, DELETE using `true`

### Junction Table Schemas
- Self-relation `{table}_relations`:
  - `id` SERIAL PK
  - `source_notion_id` TEXT NOT NULL
  - `target_notion_id` TEXT NOT NULL
  - `relation_field_name` TEXT NOT NULL
  - UNIQUE(`source_notion_id`, `target_notion_id`, `relation_field_name`)
  - Indexes on `source_notion_id`, `target_notion_id`, `relation_field_name`

- Different tables, deduplicate ON `{a}_to_{b}`:
  - `id` SERIAL PK
  - `{origin}_notion_id` TEXT NOT NULL
  - `relation_field_name` TEXT NOT NULL
  - `{related}_notion_id` TEXT NOT NULL
  - UNIQUE(`{origin}_notion_id`, `relation_field_name`, `{related}_notion_id`)
  - Indexes on `{origin}_notion_id`, `{related}_notion_id`, `relation_field_name`

- Different tables, deduplicate OFF (two directional tables):
  - Same column/constraint/index pattern as above for each direction

All junction tables have RLS enabled with the same `anon` policies as the catalog table.

### Relation Extraction Logic (SQL)
For each origin table and each relation field:
- Skip if origin/related table is unknown or origin table does not exist.
- Only read rows where `notion_data_jsonb->field` exists, is an array, and has length > 0.
- Expand related Notion IDs with `jsonb_array_elements_text(notion_data_jsonb->field)`.
- Insert rows into the corresponding junction table with `ON CONFLICT DO NOTHING` against the table’s UNIQUE constraint.

### Orchestration Functions
- `create_all_relations(environment, db_config=None, deduplicate=True)`
  - Runs end-to-end: drop catalog/junction tables, create catalog, populate it, create junctions, bulk-extract and insert relations.
- `drop_all_tables(environment, deduplicate=True)`
  - Drops catalog and all inferred junction tables.

### CLI Usage
- Flags:
  - `--environment {local|cloud}` (default: `cloud`)
  - `--dry-run` (prints what would be created; with `--debug` shows detailed breakdown and problematic IDs)
  - `--drop-all` (drops catalog and junction tables then exits)
  - `--debug` (verbose logging)
  - `--de-duplicate` (enable single-table-per-pair mode)

### Assumptions and Requirements
- Source tables exist and include `notion_id` and `notion_data_jsonb` columns.
- Relation fields in `notion_data_jsonb` are arrays of Notion page IDs.
- All tables are in schema `public`.
- `.env` provides complete DB credentials for the selected environment.


