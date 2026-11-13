/* ============================================================================
   supabase_policy_script.sql

   Idempotent fix for Supabase's `rls_disabled_in_public` advisor warning.

   For every table in the `public` schema:
     - Enables Row Level Security (no-op if already on)
     - Creates the four open anon policies (anon_select_all, anon_insert_all,
       anon_update_all, anon_delete_all) only if missing

   For every view in `public`: sets security_invoker = on so the view honours
   the underlying tables' RLS.

   Threat model: this project uses the anon key only from server-side
   automation (never shipped to a browser). RLS is enabled here for
   defence-in-depth and to clear the Supabase advisor warning — access is
   still fully open to anon, because that's what the automation needs.
   Re-run after creating new tables. Safe to run repeatedly.
   ============================================================================ */

-- STEP 1. Grant anon privileges on existing and future tables in public
GRANT USAGE ON SCHEMA public TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon;

-- STEP 2. Enable RLS + open anon policies on every public table (idempotent)
DO $$
DECLARE
  t record;
BEGIN
  FOR t IN
    SELECT tablename FROM pg_tables WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t.tablename);

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname='public' AND tablename=t.tablename AND policyname='anon_select_all'
    ) THEN
      EXECUTE format('CREATE POLICY anon_select_all ON public.%I FOR SELECT TO anon USING (true);', t.tablename);
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname='public' AND tablename=t.tablename AND policyname='anon_insert_all'
    ) THEN
      EXECUTE format('CREATE POLICY anon_insert_all ON public.%I FOR INSERT TO anon WITH CHECK (true);', t.tablename);
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname='public' AND tablename=t.tablename AND policyname='anon_update_all'
    ) THEN
      EXECUTE format('CREATE POLICY anon_update_all ON public.%I FOR UPDATE TO anon USING (true) WITH CHECK (true);', t.tablename);
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname='public' AND tablename=t.tablename AND policyname='anon_delete_all'
    ) THEN
      EXECUTE format('CREATE POLICY anon_delete_all ON public.%I FOR DELETE TO anon USING (true);', t.tablename);
    END IF;
  END LOOP;
END$$;

-- STEP 3. Views execute with invoker's privileges so underlying table RLS applies
DO $$
DECLARE
  v record;
BEGIN
  FOR v IN SELECT viewname FROM pg_views WHERE schemaname='public' LOOP
    EXECUTE format('ALTER VIEW public.%I SET (security_invoker = on);', v.viewname);
  END LOOP;
END$$;

-- STEP 4. Verify — should return zero rows
SELECT tablename
FROM pg_tables
WHERE schemaname='public'
  AND NOT rowsecurity;
