/* ============================================================================
   STEP 1. Grant anon privileges
   ---------------------------------------------------------------------------
   This ensures anon can SELECT, INSERT, UPDATE, DELETE on all *existing*
   tables (includes views and foreign tables in GRANT semantics) and also on
   all *future* tables in the `public` schema.
   ============================================================================ */

-- Allow anon to use the public schema
GRANT USAGE ON SCHEMA public TO anon;

-- Grant anon privileges on all existing tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon;

-- Ensure anon automatically gets privileges on future tables in public
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon;



/* ============================================================================
   STEP 2. Generate RLS + policy statements (tables) and invoker security (views)
   ---------------------------------------------------------------------------
   The query below outputs one SQL statement per row. For each table in
   the `public` schema, it will produce:
     - ALTER TABLE ... ENABLE ROW LEVEL SECURITY
     - CREATE POLICY anon_select_all
     - CREATE POLICY anon_insert_all
     - CREATE POLICY anon_update_all
     - CREATE POLICY anon_delete_all
   For each view in the `public` schema, it will produce:
     - ALTER VIEW ... SET (security_invoker = on)
   ============================================================================ */

-- Ensure views execute with invoker's privileges so underlying table RLS applies
SELECT 'ALTER VIEW public.' || viewname || ' SET (security_invoker = on);' AS policy_sql
FROM pg_views
WHERE schemaname = 'public'

UNION ALL

SELECT 'ALTER TABLE public.' || tablename || ' ENABLE ROW LEVEL SECURITY;' AS policy_sql
FROM pg_tables
WHERE schemaname = 'public'

UNION ALL
SELECT 'CREATE POLICY anon_select_all ON public.' || tablename || ' FOR SELECT TO anon USING (true);'
FROM pg_tables
WHERE schemaname = 'public'

UNION ALL
SELECT 'CREATE POLICY anon_insert_all ON public.' || tablename || ' FOR INSERT TO anon WITH CHECK (true);'
FROM pg_tables
WHERE schemaname = 'public'

UNION ALL
SELECT 'CREATE POLICY anon_update_all ON public.' || tablename || ' FOR UPDATE TO anon USING (true) WITH CHECK (true);'
FROM pg_tables
WHERE schemaname = 'public'

UNION ALL
SELECT 'CREATE POLICY anon_delete_all ON public.' || tablename || ' FOR DELETE TO anon USING (true);'
FROM pg_tables
WHERE schemaname = 'public'

ORDER BY policy_sql;
