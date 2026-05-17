-- =====================================================
-- COMPLETE NOTION RELATIONS SYSTEM WITH DEBUG LOGGING
-- Includes both original functions and new auxiliary view generator
-- Uses debug_log table instead of RAISE NOTICE for server debugging
-- =====================================================

-- Create debug log table

CREATE TABLE IF NOT EXISTS debug_log(
    msg text, 
    created_at timestamptz default now()
);

-- Drop all existing functions to ensure clean slate
DROP FUNCTION IF EXISTS auto_detect_all_notion_relations() CASCADE;
DROP FUNCTION IF EXISTS create_universal_relation_views() CASCADE;
DROP FUNCTION IF EXISTS smart_resolve_relations(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS analyze_relationship_patterns() CASCADE;
DROP FUNCTION IF EXISTS create_all_auxiliary_relation_views() CASCADE;
DROP FUNCTION IF EXISTS get_all_relation_columns() CASCADE;
DROP FUNCTION IF EXISTS list_auxiliary_relation_views() CASCADE;
DROP FUNCTION IF EXISTS analyze_auxiliary_relations() CASCADE;
DROP FUNCTION IF EXISTS rebuild_table_auxiliary_views(text) CASCADE;
DROP FUNCTION IF EXISTS setup_notion_relations_system() CASCADE;
DROP FUNCTION IF EXISTS apply_rls_and_view_security() CASCADE;

-- =====================================================
-- PART 1: ORIGINAL FUNCTIONS FROM FIRST SCRIPT
-- =====================================================

-- =====================================================
-- 1. UNIVERSAL RELATION DETECTOR FUNCTION
-- =====================================================

CREATE OR REPLACE FUNCTION auto_detect_all_notion_relations()
RETURNS TABLE(
    table_name text,
    relation_field text,
    field_type text,
    sample_values jsonb,
    estimated_related_table text,
    contains_notion_ids boolean,
    notion_id_percentage numeric
) AS $$
DECLARE
    v_table_rec RECORD;
    v_field_key text;
    v_field_type text;
    v_sample_data jsonb;
    v_related_table text;
    v_query text;
    v_total_values integer;
    v_notion_id_count integer;
    v_notion_id_percentage numeric;
    v_contains_notion_ids boolean;
BEGIN
    -- Loop through ALL tables that have notion_data_jsonb
    FOR v_table_rec IN 
        SELECT DISTINCT t.table_name 
        FROM information_schema.tables t
        JOIN information_schema.columns c ON t.table_name = c.table_name
        WHERE t.table_schema = 'public' 
        AND c.column_name = 'notion_data_jsonb'
        AND t.table_name LIKE 'notion_%'
    LOOP
        -- For each table, examine the JSONB structure dynamically
        v_query := format('
            SELECT DISTINCT jsonb_object_keys(notion_data_jsonb) 
            FROM %I 
            WHERE notion_data_jsonb IS NOT NULL 
            ORDER BY jsonb_object_keys(notion_data_jsonb)
        ', v_table_rec.table_name);
        
        FOR v_field_key IN EXECUTE v_query
        LOOP
            -- Get the field type
            EXECUTE format('
                SELECT jsonb_typeof(notion_data_jsonb->%L) 
                FROM %I 
                WHERE notion_data_jsonb->%L IS NOT NULL 
                LIMIT 1
            ', v_field_key, v_table_rec.table_name, v_field_key) INTO v_field_type;
            
            -- Only process array fields (potential relations)
            IF v_field_type = 'array' THEN
                -- Get sample data and validate if contains Notion IDs
                EXECUTE format('
                    WITH unnested_values AS (
                        SELECT jsonb_array_elements_text(notion_data_jsonb->%L) as elem
                        FROM %I
                        WHERE notion_data_jsonb->%L IS NOT NULL
                    ),
                    validation AS (
                        SELECT 
                            COUNT(*) as total_count,
                            COUNT(CASE 
                                WHEN elem ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' 
                                THEN 1 
                            END) as notion_id_count
                        FROM unnested_values
                        WHERE elem IS NOT NULL AND elem != ''''
                    )
                    SELECT 
                        jsonb_agg(DISTINCT elem ORDER BY elem) FILTER (WHERE elem IS NOT NULL AND elem != ''''),
                        total_count,
                        notion_id_count,
                        CASE 
                            WHEN total_count > 0 
                            THEN ROUND((notion_id_count::numeric / total_count::numeric) * 100, 2)
                            ELSE 0 
                        END as percentage
                    FROM (
                        SELECT elem FROM unnested_values LIMIT 5
                    ) sample
                    CROSS JOIN validation
                    GROUP BY total_count, notion_id_count
                ', v_field_key, v_table_rec.table_name, v_field_key) 
                INTO v_sample_data, v_total_values, v_notion_id_count, v_notion_id_percentage;
                
                -- Determine if this field contains valid Notion IDs (at least 70% should be UUIDs)
                v_contains_notion_ids := COALESCE(v_notion_id_percentage >= 70, false);
                
                -- Only proceed if it contains valid Notion IDs
                IF v_contains_notion_ids THEN
                    -- Try to determine which table this field relates to
                    v_related_table := CASE 
                        WHEN v_field_key ILIKE '%author%' THEN 'notion_connections'
                        WHEN v_field_key ILIKE '%comment%' THEN 'notion_comments'
                        WHEN v_field_key ILIKE '%illustration%' THEN 'notion_illustrations'
                        WHEN v_field_key ILIKE '%thread%' THEN 'notion_threads'
                        ELSE 'unknown'
                    END;
                    
                    -- Return the result
                    table_name := v_table_rec.table_name;
                    relation_field := v_field_key;
                    field_type := v_field_type;
                    sample_values := v_sample_data;
                    estimated_related_table := v_related_table;
                    contains_notion_ids := v_contains_notion_ids;
                    notion_id_percentage := COALESCE(v_notion_id_percentage, 0);
                    
                    RETURN NEXT;
                END IF;
            END IF;
        END LOOP;
    END LOOP;
    
    RETURN;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 2. UNIVERSAL RELATION VIEW CREATOR
-- =====================================================

CREATE OR REPLACE FUNCTION create_universal_relation_views()
RETURNS void AS $$
DECLARE
    v_table_rec RECORD;
    v_view_sql text;
    v_view_name text;
    v_field_key text;
    v_field_type text;
    v_computed_columns text := '';
    v_query text;
    v_total_values integer;
    v_notion_id_count integer;
    v_notion_id_percentage numeric;
    v_contains_notion_ids boolean;
BEGIN
    -- Drop existing universal views
    FOR v_table_rec IN 
        SELECT viewname 
        FROM pg_views 
        WHERE schemaname = 'public' 
        AND viewname LIKE '%_universal_relations'
    LOOP
        EXECUTE 'DROP VIEW IF EXISTS ' || quote_ident(v_table_rec.viewname) || ' CASCADE';
    END LOOP;
    
    -- Create universal relation views for each table
    FOR v_table_rec IN 
        SELECT t.table_name 
        FROM information_schema.tables t
        WHERE t.table_schema = 'public' 
        AND t.table_name LIKE 'notion_%'
        AND t.table_name IN (
            SELECT c.table_name 
            FROM information_schema.columns c
            WHERE c.column_name = 'notion_data_jsonb'
        )
    LOOP
        v_view_name := v_table_rec.table_name || '_universal_relations';
        v_computed_columns := '';
        
        -- Get all field keys from this table's JSONB data
        v_query := format('
            SELECT DISTINCT jsonb_object_keys(notion_data_jsonb) 
            FROM %I 
            WHERE notion_data_jsonb IS NOT NULL 
            ORDER BY jsonb_object_keys(notion_data_jsonb)
        ', v_table_rec.table_name);
        
        FOR v_field_key IN EXECUTE v_query
        LOOP
            -- Check if it's an array field
            EXECUTE format('
                SELECT jsonb_typeof(notion_data_jsonb->%L) 
                FROM %I 
                WHERE notion_data_jsonb->%L IS NOT NULL 
                LIMIT 1
            ', v_field_key, v_table_rec.table_name, v_field_key) INTO v_field_type;
            
            -- Only process array fields (potential relations)
            IF v_field_type = 'array' THEN
                -- Validate if this field contains Notion IDs
                EXECUTE format('
                    WITH unnested_values AS (
                        SELECT jsonb_array_elements_text(notion_data_jsonb->%L) as elem
                        FROM %I
                        WHERE notion_data_jsonb->%L IS NOT NULL
                    )
                    SELECT 
                        COUNT(*) as total_count,
                        COUNT(CASE 
                            WHEN elem ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' 
                            THEN 1 
                        END) as notion_id_count
                    FROM unnested_values
                    WHERE elem IS NOT NULL AND elem != ''''
                ', v_field_key, v_table_rec.table_name, v_field_key) 
                INTO v_total_values, v_notion_id_count;
                
                -- Calculate percentage and check if valid
                v_notion_id_percentage := CASE 
                    WHEN v_total_values > 0 
                    THEN ROUND((v_notion_id_count::numeric / v_total_values::numeric) * 100, 2)
                    ELSE 0 
                END;
                
                v_contains_notion_ids := v_notion_id_percentage >= 70;
                
                -- Only add array fields that contain valid Notion IDs
                IF v_contains_notion_ids THEN
                    IF length(v_computed_columns) > 0 THEN
                        v_computed_columns := v_computed_columns || ', ';
                    END IF;
                    
                    v_computed_columns := v_computed_columns || format('
                        notion_data_jsonb->%L as %I',
                        v_field_key, 
                        'rel_' || lower(regexp_replace(v_field_key, '[^a-zA-Z0-9_]', '_', 'g'))
                    );
                END IF;
            END IF;
        END LOOP;
        
        -- Create the universal view if we have computed columns
        IF length(v_computed_columns) > 0 THEN
            v_view_sql := format('
                CREATE OR REPLACE VIEW %I AS
                SELECT 
                    t.*,
                    %s
                FROM %I t
                WHERE t.notion_data_jsonb IS NOT NULL
            ', v_view_name, v_computed_columns, v_table_rec.table_name);
            
            BEGIN
                EXECUTE v_view_sql;
                INSERT INTO debug_log(msg) VALUES ('Created universal relation view: ' || v_view_name);
            EXCEPTION WHEN OTHERS THEN
                INSERT INTO debug_log(msg) VALUES ('Error creating view ' || v_view_name || ': ' || SQLERRM);
            END;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 3. SMART RELATIONSHIP RESOLVER
-- =====================================================

CREATE OR REPLACE FUNCTION smart_resolve_relations(
    p_source_table text,
    p_relation_field text,
    p_target_table text DEFAULT NULL
)
RETURNS TABLE(
    source_id text,
    source_name text,
    relation_type text,
    target_id text,
    target_name text,
    target_table_name text
) AS $$
DECLARE
    v_actual_target_table text;
    v_target_name_field text;
    v_query text;
BEGIN
    -- If no target table specified, try to auto-detect
    IF p_target_table IS NULL THEN
        -- Simple detection based on field name
        v_actual_target_table := CASE 
            WHEN p_relation_field ILIKE '%author%' THEN 'notion_connections'
            WHEN p_relation_field ILIKE '%comment%' THEN 'notion_comments'
            WHEN p_relation_field ILIKE '%illustration%' THEN 'notion_illustrations'
            WHEN p_relation_field ILIKE '%thread%' THEN 'notion_threads'
            ELSE 'notion_connections'  -- default fallback
        END;
    ELSE
        v_actual_target_table := p_target_table;
    END IF;
    
    -- Determine the name field in the target table
    IF EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_name = v_actual_target_table AND c.column_name = 'name'
    ) THEN
        v_target_name_field := 'name';
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_name = v_actual_target_table AND c.column_name = 'title'
    ) THEN
        v_target_name_field := 'title';
    ELSE
        v_target_name_field := 'notion_id';
    END IF;
    
    -- Return the resolved relations
    v_query := format('
        SELECT 
            s.notion_id as source_id,
            COALESCE(s.name, s.title, s.notion_id) as source_name,
            %L as relation_type,
            relation_id as target_id,
            COALESCE(t.%I, t.notion_id) as target_name,
            %L as target_table_name
        FROM %I s
        CROSS JOIN LATERAL jsonb_array_elements_text(
            s.notion_data_jsonb->%L
        ) AS relation_id
        LEFT JOIN %I t ON t.notion_id = relation_id
        WHERE s.notion_data_jsonb->%L IS NOT NULL 
        AND jsonb_typeof(s.notion_data_jsonb->%L) = ''array''
    ', p_relation_field, v_target_name_field, v_actual_target_table, 
       p_source_table, p_relation_field, v_actual_target_table, 
       p_relation_field, p_relation_field);
    
    RETURN QUERY EXECUTE v_query;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 4. AUTOMATIC RELATIONSHIP ANALYSIS
-- =====================================================

CREATE OR REPLACE FUNCTION analyze_relationship_patterns()
RETURNS TABLE(
    source_table text,
    relation_field text,
    target_table text,
    relation_count bigint,
    unique_targets bigint,
    avg_relations_per_source numeric
) AS $$
DECLARE
    v_table_rec RECORD;
    v_field_key text;
    v_field_type text;
    v_query text;
    v_count bigint;
    v_unique bigint;
    v_avg numeric;
    v_target_table text;
BEGIN
    -- For each table with JSONB data
    FOR v_table_rec IN 
        SELECT t.table_name 
        FROM information_schema.tables t
        WHERE t.table_schema = 'public' 
        AND t.table_name LIKE 'notion_%'
        AND EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_name = t.table_name 
            AND c.column_name = 'notion_data_jsonb'
        )
    LOOP
        -- Get all field keys from this table
        v_query := format('
            SELECT DISTINCT jsonb_object_keys(notion_data_jsonb) 
            FROM %I 
            WHERE notion_data_jsonb IS NOT NULL
            ORDER BY jsonb_object_keys(notion_data_jsonb)
        ', v_table_rec.table_name);
        
        FOR v_field_key IN EXECUTE v_query
        LOOP
            -- Check if it's an array field
            EXECUTE format('
                SELECT jsonb_typeof(notion_data_jsonb->%L) 
                FROM %I 
                WHERE notion_data_jsonb->%L IS NOT NULL 
                LIMIT 1
            ', v_field_key, v_table_rec.table_name, v_field_key) INTO v_field_type;
            
            IF v_field_type = 'array' THEN
                -- Estimate target table
                v_target_table := CASE 
                    WHEN v_field_key ILIKE '%author%' THEN 'notion_connections'
                    WHEN v_field_key ILIKE '%comment%' THEN 'notion_comments'
                    WHEN v_field_key ILIKE '%illustration%' THEN 'notion_illustrations'
                    WHEN v_field_key ILIKE '%thread%' THEN 'notion_threads'
                    ELSE 'unknown'
                END;
                
                -- Calculate statistics
                EXECUTE format('
                    SELECT 
                        COUNT(*),
                        COUNT(DISTINCT elem),
                        COUNT(*)::numeric / NULLIF(COUNT(DISTINCT notion_id), 0)
                    FROM %I
                    CROSS JOIN LATERAL jsonb_array_elements_text(notion_data_jsonb->%L) AS elem
                    WHERE notion_data_jsonb->%L IS NOT NULL
                ', v_table_rec.table_name, v_field_key, v_field_key) 
                INTO v_count, v_unique, v_avg;
                
                -- Return the result
                source_table := v_table_rec.table_name;
                relation_field := v_field_key;
                target_table := v_target_table;
                relation_count := COALESCE(v_count, 0);
                unique_targets := COALESCE(v_unique, 0);
                avg_relations_per_source := COALESCE(v_avg, 0);
                
                RETURN NEXT;
            END IF;
        END LOOP;
    END LOOP;
    
    RETURN;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 5. FUNCTION TO DISCOVER ALL REL_ COLUMNS
-- =====================================================

CREATE OR REPLACE FUNCTION get_all_relation_columns()
RETURNS TABLE(
    table_name  text,
    column_name text,
    field_name  text
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_view_rec RECORD;
    v_column_rec RECORD;
    v_query text;
BEGIN
    -- Loop through all universal_relations views
    FOR v_view_rec IN 
        SELECT t.table_name 
        FROM information_schema.tables t
        WHERE t.table_schema = 'public' 
        AND t.table_type = 'VIEW'
        AND t.table_name LIKE '%_universal_relations'
    LOOP
        -- For each view, get all rel_ columns
        FOR v_column_rec IN
            SELECT c.column_name
            FROM information_schema.columns c
            WHERE c.table_schema = 'public'
            AND c.table_name = v_view_rec.table_name
            AND c.column_name LIKE 'rel_%'
        LOOP
            table_name := v_view_rec.table_name;
            column_name := v_column_rec.column_name;
            field_name := substring(v_column_rec.column_name FROM 5); -- Remove 'rel_' prefix
            RETURN NEXT;
        END LOOP;
    END LOOP;
END;
$$;

-- =====================================================
-- 6. MAIN FUNCTION TO CREATE ALL AUXILIARY VIEWS
-- =====================================================

CREATE OR REPLACE FUNCTION create_all_auxiliary_relation_views()
RETURNS void AS $$
DECLARE
    v_rel_column RECORD;
    v_view_name text;
    v_field_id_name text;
    v_view_sql text;
    v_drop_sql text;
    v_total_created integer := 0;
    v_total_errors integer := 0;
BEGIN
    INSERT INTO debug_log(msg) VALUES ('🚀 Starting creation of auxiliary relation views...');
    INSERT INTO debug_log(msg) VALUES ('================================================');
    
    -- Loop through all rel_ columns discovered
    FOR v_rel_column IN 
        SELECT * FROM get_all_relation_columns()
    LOOP
        -- Construct the view name: strip "_universal_relations" before appending the rel_ column
        v_view_name :=
        regexp_replace(
            regexp_replace(
            concat_ws('_',
                rtrim(regexp_replace(v_rel_column.table_name, '_universal_relations$', ''), '_'),
                ltrim(v_rel_column.column_name, '_')
            ),
            '_+', '_', 'g'
            ),
            '^_+|_+$', '', 'g'
        );

        -- Construct the field ID column name
        v_field_id_name := v_rel_column.field_name || '_id';
        
        INSERT INTO debug_log(msg) VALUES ('📦 Processing: ' || v_rel_column.table_name || '.' || v_rel_column.column_name || ' -> View: ' || v_view_name);
        
        -- Drop the view if it already exists
        v_drop_sql := format('DROP VIEW IF EXISTS %I CASCADE', v_view_name);
        
        -- FIXED: Construct the CREATE VIEW SQL with proper JSONB handling
        v_view_sql := format('
            CREATE OR REPLACE VIEW %I AS
            SELECT 
                t.notion_id,
                elem_text AS %I
            FROM %I t
            CROSS JOIN LATERAL (
                SELECT jsonb_array_elements_text(
                    CASE
                        -- Use the rel_* column in the universal view if it is a JSONB array
                        WHEN jsonb_typeof(t.%I) = ''array'' THEN t.%I
                        -- Fallback to the raw JSONB field on the base record
                        WHEN jsonb_typeof(t.notion_data_jsonb->%L) = ''array'' THEN t.notion_data_jsonb->%L
                        ELSE ''[]''::jsonb
                    END
                ) AS elem_text
            ) j
            WHERE elem_text IS NOT NULL
            AND elem_text <> ''''
            AND elem_text ~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$''
        ',
            v_view_name,                   -- View name
            v_field_id_name,               -- Expanded ID column alias
            v_rel_column.table_name,       -- Source universal_relations view
            v_rel_column.column_name,      -- rel_* column in the view (JSONB)
            v_rel_column.column_name,      -- rel_* column again
            v_rel_column.field_name,       -- JSONB key (fallback)
            v_rel_column.field_name        -- JSONB key again
        );

        
        -- Try to create the view
        BEGIN
            -- Drop existing view first
            EXECUTE v_drop_sql;
            
            -- Create the new view
            EXECUTE v_view_sql;
            
            v_total_created := v_total_created + 1;
            INSERT INTO debug_log(msg) VALUES ('   ✅ Created view: ' || v_view_name);
            
        EXCEPTION WHEN OTHERS THEN
            v_total_errors := v_total_errors + 1;
            INSERT INTO debug_log(msg) VALUES ('   ❌ Error creating view ' || v_view_name || ': ' || SQLERRM);
        END;
        
    END LOOP;
    
    INSERT INTO debug_log(msg) VALUES ('================================================');
    INSERT INTO debug_log(msg) VALUES ('🎉 Auxiliary view creation complete!');
    INSERT INTO debug_log(msg) VALUES ('   ✅ Successfully created: ' || v_total_created || ' views');
    IF v_total_errors > 0 THEN
        INSERT INTO debug_log(msg) VALUES ('   ⚠️  Errors encountered: ' || v_total_errors || ' views');
    END IF;
    INSERT INTO debug_log(msg) VALUES ('================================================');
    
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 7. HELPER FUNCTION TO LIST ALL AUXILIARY VIEWS
-- =====================================================

CREATE OR REPLACE FUNCTION list_auxiliary_relation_views()
RETURNS TABLE(
    view_name text,
    source_table text,
    relation_field text,
    id_column text,
    row_count bigint
) AS $$
DECLARE
    v_view_rec RECORD;
    v_count bigint;
    v_query text;
BEGIN
    FOR v_view_rec IN 
        SELECT 
            v.viewname,
            -- Parse source table from view name
            split_part(v.viewname, '_rel_', 1) as src_table,
            -- Parse relation field from view name
            'rel_' || split_part(v.viewname, '_rel_', 2) as rel_field,
            -- Construct the ID column name
            split_part(v.viewname, '_rel_', 2) || '_id' as id_col
        FROM pg_views v
        WHERE v.schemaname = 'public'
        AND v.viewname LIKE 'notion_%_rel_%'
        ORDER BY v.viewname
    LOOP
        -- Get row count for this view
        v_query := format('SELECT COUNT(*) FROM %I', v_view_rec.viewname);
        EXECUTE v_query INTO v_count;
        
        view_name := v_view_rec.viewname;
        source_table := v_view_rec.src_table;
        relation_field := v_view_rec.rel_field;
        id_column := v_view_rec.id_col;
        row_count := v_count;
        
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 8. ANALYSIS FUNCTION FOR AUXILIARY VIEWS
-- =====================================================

CREATE OR REPLACE FUNCTION analyze_auxiliary_relations()
RETURNS TABLE(
    source_table text,
    relation_field text,
    total_relations bigint,
    unique_sources bigint,
    unique_targets bigint,
    avg_relations_per_source numeric
) AS $$
DECLARE
    v_view_rec RECORD;
    v_stats RECORD;
    v_query text;
BEGIN
    FOR v_view_rec IN 
        SELECT * FROM list_auxiliary_relation_views()
    LOOP
        v_query := format('
            SELECT 
                COUNT(*) as total_rels,
                COUNT(DISTINCT notion_id) as unique_srcs,
                COUNT(DISTINCT %I) as unique_tgts,
                COUNT(*)::numeric / NULLIF(COUNT(DISTINCT notion_id), 0) as avg_per_src
            FROM %I
        ', v_view_rec.id_column, v_view_rec.view_name);
        
        EXECUTE v_query INTO v_stats;
        
        source_table := v_view_rec.source_table;
        relation_field := v_view_rec.relation_field;
        total_relations := v_stats.total_rels;
        unique_sources := v_stats.unique_srcs;
        unique_targets := v_stats.unique_tgts;
        avg_relations_per_source := ROUND(v_stats.avg_per_src, 2);
        
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 9. UTILITY: REBUILD SINGLE TABLE'S AUXILIARY VIEWS
-- =====================================================

CREATE OR REPLACE FUNCTION rebuild_table_auxiliary_views(p_table_name text)
RETURNS void AS $$
DECLARE
    v_rel_column RECORD;
    v_count integer := 0;
BEGIN
    INSERT INTO debug_log(msg) VALUES ('🔄 Rebuilding auxiliary views for table: ' || p_table_name);
    
    FOR v_rel_column IN 
        SELECT * FROM get_all_relation_columns()
        WHERE table_name = p_table_name
    LOOP
        -- Drop and recreate each view
        PERFORM create_all_auxiliary_relation_views();
        v_count := v_count + 1;
    END LOOP;
    
    INSERT INTO debug_log(msg) VALUES ('   ✅ Rebuilt ' || v_count || ' auxiliary views for ' || p_table_name);
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 10. APPLY RLS POLICIES & VIEW SECURITY (idempotent)
-- =====================================================

CREATE OR REPLACE FUNCTION apply_rls_and_view_security()
RETURNS void AS $$
DECLARE
    v_role text;          -- either 'anon' or 'PUBLIC'
    v_role_sql text;      -- either quote_ident('anon') or literal 'PUBLIC'
    r_tbl RECORD;
    r_view RECORD;
    has_debug boolean := false;
BEGIN
    -- Optional debug_log detection
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema='public' AND table_name='debug_log'
    ) INTO has_debug;

    IF has_debug THEN
        INSERT INTO debug_log(msg) VALUES ('🔐 Applying RLS policies and view security in schema public');
    END IF;

    -- Prefer 'anon' role; otherwise use PUBLIC keyword
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        v_role := 'anon';
        v_role_sql := quote_ident('anon');  -- becomes "anon"
    ELSE
        v_role := 'PUBLIC';
        v_role_sql := 'PUBLIC';             -- keep as keyword
    END IF;

    -- 1) Enable RLS + policies for every table in public
    FOR r_tbl IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname='public'
    LOOP
        -- Enable RLS (idempotent)
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r_tbl.schemaname, r_tbl.tablename);

        -- CREATE POLICY anon_select_all (if missing)
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = r_tbl.schemaname
              AND tablename  = r_tbl.tablename
              AND policyname = 'anon_select_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY anon_select_all ON %I.%I FOR SELECT TO %s USING (true)',
                r_tbl.schemaname, r_tbl.tablename, v_role_sql
            );
        END IF;

        -- CREATE POLICY anon_insert_all (if missing)
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = r_tbl.schemaname
              AND tablename  = r_tbl.tablename
              AND policyname = 'anon_insert_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY anon_insert_all ON %I.%I FOR INSERT TO %s WITH CHECK (true)',
                r_tbl.schemaname, r_tbl.tablename, v_role_sql
            );
        END IF;

        -- CREATE POLICY anon_update_all (if missing)
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = r_tbl.schemaname
              AND tablename  = r_tbl.tablename
              AND policyname = 'anon_update_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY anon_update_all ON %I.%I FOR UPDATE TO %s USING (true) WITH CHECK (true)',
                r_tbl.schemaname, r_tbl.tablename, v_role_sql
            );
        END IF;

        -- CREATE POLICY anon_delete_all (if missing)
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = r_tbl.schemaname
              AND tablename  = r_tbl.tablename
              AND policyname = 'anon_delete_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY anon_delete_all ON %I.%I FOR DELETE TO %s USING (true)',
                r_tbl.schemaname, r_tbl.tablename, v_role_sql
            );
        END IF;

        IF has_debug THEN
            INSERT INTO debug_log(msg) VALUES (
                format('   ✅ RLS applied to table %I.%I (role: %s)', r_tbl.schemaname, r_tbl.tablename, v_role)
            );
        END IF;
    END LOOP;

    -- 2) Set security_invoker = on for every view in public (PG15+)
    FOR r_view IN
        SELECT schemaname, viewname
        FROM pg_views
        WHERE schemaname='public'
    LOOP
        BEGIN
            EXECUTE format('ALTER VIEW %I.%I SET (security_invoker = on)', r_view.schemaname, r_view.viewname);
            IF has_debug THEN
                INSERT INTO debug_log(msg) VALUES (
                    format('   🛡️  security_invoker=on for view %I.%I', r_view.schemaname, r_view.viewname)
                );
            END IF;
        EXCEPTION WHEN OTHERS THEN
            -- Older PostgreSQL versions won't support this; log and continue
            IF has_debug THEN
                INSERT INTO debug_log(msg) VALUES (
                    format('   ⚠️  Could not set security_invoker for view %I.%I: %s', r_view.schemaname, r_view.viewname, SQLERRM)
                );
            END IF;
        END;
    END LOOP;

    IF has_debug THEN
        INSERT INTO debug_log(msg) VALUES ('🎉 RLS policy & view security application complete');
    END IF;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 11. SETUP FUNCTION 
-- =====================================================

CREATE OR REPLACE FUNCTION setup_notion_relations_system()
RETURNS void AS $$
DECLARE
    v_rel_columns_count integer;
    v_views_before integer;
    v_views_after integer;
BEGIN
    
    PERFORM clear_debug_log();

    INSERT INTO debug_log(msg) VALUES ('🗒️ Cleared log...');
    INSERT INTO debug_log(msg) VALUES ('================================================');

    INSERT INTO debug_log(msg) VALUES ('🚀 Setting up complete Notion relations system...');
    INSERT INTO debug_log(msg) VALUES ('================================================');
     
    -- Count existing auxiliary views
    SELECT COUNT(*) INTO v_views_before
    FROM pg_views 
    WHERE schemaname = 'public' 
    AND viewname LIKE 'notion_%_rel_%';
     
    -- Step 1: Create universal relation views
    INSERT INTO debug_log(msg) VALUES ('📦 Step 1: Creating universal relation views...');
    PERFORM create_universal_relation_views();
    INSERT INTO debug_log(msg) VALUES ('   ✅ Universal relation views created');
    
    -- Count discovered relation columns
    SELECT COUNT(*) INTO v_rel_columns_count
    FROM get_all_relation_columns();
    
    INSERT INTO debug_log(msg) VALUES ('📦 Step 2: Creating auxiliary relation views...');
    INSERT INTO debug_log(msg) VALUES ('   🔍 Found ' || v_rel_columns_count || ' relation columns to process');
    
    -- Step 2: Create all auxiliary views
    PERFORM create_all_auxiliary_relation_views();

    INSERT INTO debug_log(msg) VALUES ('📦 Step 3: Applying policies...');
    
    -- Step 2: Create all auxiliary views
    PERFORM apply_rls_and_view_security();
    
    -- Count auxiliary views after creation
    SELECT COUNT(*) INTO v_views_after
    FROM pg_views 
    WHERE schemaname = 'public' 
    AND viewname LIKE 'notion_%_rel_%';
    
    INSERT INTO debug_log(msg) VALUES ('================================================');
    INSERT INTO debug_log(msg) VALUES ('🎉 Setup complete! System statistics:');
    INSERT INTO debug_log(msg) VALUES ('   📊 Auxiliary views before: ' || v_views_before);
    INSERT INTO debug_log(msg) VALUES ('   📊 Auxiliary views after: ' || v_views_after);
    INSERT INTO debug_log(msg) VALUES ('   📊 New views created: ' || (v_views_after - v_views_before));
    INSERT INTO debug_log(msg) VALUES ('================================================');
    INSERT INTO debug_log(msg) VALUES ('');
    INSERT INTO debug_log(msg) VALUES ('📚 Quick usage guide:');
    INSERT INTO debug_log(msg) VALUES ('   • List all auxiliary views: SELECT * FROM list_auxiliary_relation_views();');
    INSERT INTO debug_log(msg) VALUES ('   • Analyze relations: SELECT * FROM analyze_auxiliary_relations();');
    INSERT INTO debug_log(msg) VALUES ('   • Query specific view: SELECT * FROM notion_<table>_rel_<field>;');
    INSERT INTO debug_log(msg) VALUES ('');
    INSERT INTO debug_log(msg) VALUES ('🔗 Example JOIN query:');
    INSERT INTO debug_log(msg) VALUES ('   SELECT a.*, c.name as author_name');
    INSERT INTO debug_log(msg) VALUES ('   FROM notion_articles a');
    INSERT INTO debug_log(msg) VALUES ('   JOIN notion_articles_rel_author ra ON a.notion_id = ra.notion_id');
    INSERT INTO debug_log(msg) VALUES ('   JOIN notion_connections c ON ra.author_id = c.notion_id;');
    
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 12. SIMPLE SUMMARY VIEW
-- =====================================================

CREATE OR REPLACE VIEW notion_all_relations_summary AS
WITH detected_relations AS (
    SELECT * FROM auto_detect_all_notion_relations()
)
SELECT 
    table_name as source_table,
    relation_field,
    field_type,
    estimated_related_table as target_table,
    'Detected via auto_detect function' as detection_method
FROM detected_relations
ORDER BY table_name, relation_field;

-- =====================================================
-- 13. DEBUG HELPER FUNCTIONS
-- =====================================================

-- Function to clear debug log
CREATE OR REPLACE FUNCTION clear_debug_log()
RETURNS void AS $$
BEGIN
    DELETE FROM debug_log;
    INSERT INTO debug_log(msg) VALUES ('🧹 Debug log cleared');
END;
$$ LANGUAGE plpgsql;

-- Function to view recent debug messages
CREATE OR REPLACE FUNCTION view_debug_log(p_limit integer DEFAULT 50)
RETURNS TABLE(
    msg text,
    created_at timestamptz
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        d.msg,
        d.created_at
    FROM debug_log d
    ORDER BY d.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Function to view debug messages from specific timeframe
CREATE OR REPLACE FUNCTION view_debug_log_since(p_since timestamptz)
RETURNS TABLE(
    msg text,
    created_at timestamptz
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        d.msg,
        d.created_at
    FROM debug_log d
    WHERE d.created_at >= p_since
    ORDER BY d.created_at DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to count errors in debug log
CREATE OR REPLACE FUNCTION count_debug_errors()
RETURNS TABLE(
    error_count bigint,
    latest_error text,
    latest_error_time timestamptz
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COUNT(*) as error_count,
        (SELECT msg FROM debug_log WHERE msg ILIKE '%error%' OR msg LIKE '%❌%' ORDER BY created_at DESC LIMIT 1) as latest_error,
        (SELECT created_at FROM debug_log WHERE msg ILIKE '%error%' OR msg LIKE '%❌%' ORDER BY created_at DESC LIMIT 1) as latest_error_time
    FROM debug_log
    WHERE msg ILIKE '%error%' OR msg LIKE '%❌%';
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- USAGE INSTRUCTIONS WITH DEBUG FEATURES
-- =====================================================

/*
═══════════════════════════════════════════════════════════════
COMPLETE NOTION RELATIONS SYSTEM WITH DEBUG LOGGING
═══════════════════════════════════════════════════════════════

🚀 INITIAL SETUP:
-----------------
SELECT setup_notion_relations_system();

This will log all progress to the debug_log table for monitoring.

📊 VIEWING SETUP PROGRESS:
--------------------------
-- View recent debug messages:
SELECT * FROM view_debug_log(100);

-- View messages from the last hour:
SELECT * FROM view_debug_log_since(NOW() - INTERVAL '1 hour');

-- Check for errors:
SELECT * FROM count_debug_errors();

-- Clear debug log when needed:
SELECT clear_debug_log();

📈 VIEWING RESULTS:
-------------------
-- See all detected relations:
SELECT * FROM auto_detect_all_notion_relations();

-- See all auxiliary views created:
SELECT * FROM list_auxiliary_relation_views();

-- Analyze relationship statistics:
SELECT * FROM analyze_auxiliary_relations();

-- Check relationship patterns:
SELECT * FROM analyze_relationship_patterns();

-- Get summary of all relations:
SELECT * FROM notion_all_relations_summary;

🔍 QUERYING DATA:
-----------------
Universal Views (all relations as JSON arrays):
- notion_articles_universal_relations
- notion_books_universal_relations
- notion_illustrations_universal_relations

Auxiliary Views (normalized, one row per relation):
- notion_articles_rel_author
- notion_illustrations_rel_publishli
- notion_books_rel_connections

📝 EXAMPLE QUERIES:
-------------------
-- Find all authors of an article using auxiliary view:
SELECT 
    a.notion_id,
    a.title,
    c.name as author_name
FROM notion_articles a
JOIN notion_articles_rel_author ra ON a.notion_id = ra.notion_id
JOIN notion_connections c ON ra.author_id = c.notion_id
WHERE a.title LIKE '%Technology%';

-- Use smart resolver for specific relations:
SELECT * FROM smart_resolve_relations('notion_articles', 'author or source');

-- Count relations per source:
SELECT 
    notion_id,
    COUNT(*) as relation_count
FROM notion_illustrations_rel_publishli
GROUP BY notion_id
ORDER BY relation_count DESC;

🔄 MAINTENANCE & DEBUGGING:
---------------------------
-- Rebuild everything:
SELECT setup_notion_relations_system();

-- Rebuild only auxiliary views:
SELECT create_all_auxiliary_relation_views();

-- Rebuild for specific table:
SELECT rebuild_table_auxiliary_views('notion_articles');

-- Monitor setup progress:
SELECT * FROM view_debug_log(50);

-- Check for setup errors:
SELECT * FROM count_debug_errors();

-- Clear old debug messages:
SELECT clear_debug_log();

-- Check system health:
SELECT 
    'Total Tables' as metric,
    COUNT(DISTINCT table_name) as count
FROM get_all_relation_columns()
UNION ALL
SELECT 
    'Total Relation Columns',
    COUNT(*)
FROM get_all_relation_columns()
UNION ALL
SELECT 
    'Total Auxiliary Views',
    COUNT(*)
FROM list_auxiliary_relation_views();

🐛 TROUBLESHOOTING:
-------------------
-- If setup fails, check debug log for errors:
SELECT * FROM view_debug_log_since(NOW() - INTERVAL '10 minutes')
WHERE msg ILIKE '%error%' OR msg LIKE '%❌%';

-- If views are missing, check for rel_ columns:
SELECT * FROM get_all_relation_columns();

-- If rel_ columns are missing, recreate auxiliary views:
SELECT create_all_auxiliary_relation_views();

-- Check for errors in specific table:
SELECT * FROM get_all_relation_columns() 
WHERE table_name = 'notion_articles';

-- View all debug messages for troubleshooting:
SELECT msg, created_at FROM debug_log 
ORDER BY created_at DESC;

═══════════════════════════════════════════════════════════════
*/