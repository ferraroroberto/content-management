-- =====================================================
-- AUTOMATIC NOTION RELATIONS DETECTOR & HANDLER
-- FIXED VERSION - All ambiguity issues resolved
-- =====================================================

-- Drop existing functions to ensure clean slate
DROP FUNCTION IF EXISTS auto_detect_all_notion_relations() CASCADE;
DROP FUNCTION IF EXISTS create_dynamic_relation_columns() CASCADE;
DROP FUNCTION IF EXISTS create_universal_relation_views() CASCADE;
DROP FUNCTION IF EXISTS smart_resolve_relations(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS create_automatic_relation_indexes() CASCADE;
DROP FUNCTION IF EXISTS analyze_relationship_patterns() CASCADE;

-- =====================================================
-- 1. UNIVERSAL RELATION DETECTOR FUNCTION (FIXED)
-- =====================================================

CREATE OR REPLACE FUNCTION auto_detect_all_notion_relations()
RETURNS TABLE(
    table_name text,
    relation_field text,
    field_type text,
    sample_values jsonb,
    estimated_related_table text
) AS $$
DECLARE
    v_table_rec RECORD;
    v_field_key text;
    v_field_type text;
    v_sample_data jsonb;
    v_related_table text;
    v_query text;
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
            LIMIT 100
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
                -- Get sample data for this field
                EXECUTE format('
                    SELECT jsonb_agg(DISTINCT elem) 
                    FROM (
                        SELECT jsonb_array_elements(notion_data_jsonb->%L) as elem
                        FROM %I
                        WHERE notion_data_jsonb->%L IS NOT NULL
                        LIMIT 5
                    ) sub
                ', v_field_key, v_table_rec.table_name, v_field_key) INTO v_sample_data;
                
                -- Try to determine which table this field relates to
                -- (simplified for now - you can enhance this logic)
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
                
                RETURN NEXT;
            END IF;
        END LOOP;
    END LOOP;
    
    RETURN;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 2. DYNAMIC COMPUTED COLUMN CREATOR (FIXED)
-- =====================================================

CREATE OR REPLACE FUNCTION create_dynamic_relation_columns()
RETURNS void AS $$
DECLARE
    v_table_rec RECORD;
    v_field_key text;
    v_field_type text;
    v_column_sql text;
    v_col_name text;
    v_query text;
BEGIN
    -- For each table, add computed columns for all relation fields
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
        RAISE NOTICE 'Processing table: %', v_table_rec.table_name;
        
        -- Get all field keys from this table's JSONB data
        v_query := format('
            SELECT DISTINCT jsonb_object_keys(notion_data_jsonb) 
            FROM %I 
            WHERE notion_data_jsonb IS NOT NULL 
            LIMIT 100
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
                -- Create a safe column name
                v_col_name := 'rel_' || lower(regexp_replace(v_field_key, '[^a-zA-Z0-9_]', '_', 'g'));
                
                -- Check if column already exists
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns c
                    WHERE c.table_name = v_table_rec.table_name 
                    AND c.column_name = v_col_name
                ) THEN
                    -- Create computed column that automatically expands the relation
                    v_column_sql := format('
                        ALTER TABLE %I 
                        ADD COLUMN %I text[] GENERATED ALWAYS AS (
                            CASE 
                                WHEN jsonb_typeof(notion_data_jsonb->%L) = ''array'' 
                                THEN ARRAY(
                                    SELECT jsonb_array_elements_text(notion_data_jsonb->%L)
                                )
                                ELSE ARRAY[]::text[]
                            END
                        ) STORED;
                    ', v_table_rec.table_name, v_col_name, v_field_key, v_field_key);
                    
                    BEGIN
                        EXECUTE v_column_sql;
                        RAISE NOTICE 'Added computed column % to table %', v_col_name, v_table_rec.table_name;
                    EXCEPTION WHEN OTHERS THEN
                        RAISE NOTICE 'Error adding column % to table %: %', v_col_name, v_table_rec.table_name, SQLERRM;
                    END;
                END IF;
            END IF;
        END LOOP;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 3. UNIVERSAL RELATION VIEW CREATOR (FIXED)
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
            LIMIT 100
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
            
            -- Only add array fields (relations)
            IF v_field_type = 'array' THEN
                IF length(v_computed_columns) > 0 THEN
                    v_computed_columns := v_computed_columns || ', ';
                END IF;
                
                v_computed_columns := v_computed_columns || format('
                    notion_data_jsonb->%L as %I',
                    v_field_key, 
                    'rel_' || lower(regexp_replace(v_field_key, '[^a-zA-Z0-9_]', '_', 'g'))
                );
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
                RAISE NOTICE 'Created universal relation view: %', v_view_name;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'Error creating view %: %', v_view_name, SQLERRM;
            END;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 4. SMART RELATIONSHIP RESOLVER (FIXED)
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
-- 5. AUTOMATIC RELATIONSHIP ANALYSIS (FIXED)
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
            LIMIT 100
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
-- 6. PERFORMANCE OPTIMIZATION (FIXED)
-- =====================================================

CREATE OR REPLACE FUNCTION create_automatic_relation_indexes()
RETURNS void AS $$
DECLARE
    v_table_rec RECORD;
    v_field_key text;
    v_field_type text;
    v_index_name text;
    v_index_sql text;
    v_query text;
BEGIN
    -- For each table, create indexes on all array fields
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
            LIMIT 100
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
                v_index_name := 'idx_' || substr(v_table_rec.table_name, 1, 20) || '_' || 
                               substr(lower(regexp_replace(v_field_key, '[^a-zA-Z0-9_]', '_', 'g')), 1, 20);
                
                -- Check if index already exists
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes 
                    WHERE indexname = v_index_name
                ) THEN
                    v_index_sql := format('
                        CREATE INDEX %I ON %I 
                        USING GIN ((notion_data_jsonb->%L))
                    ', v_index_name, v_table_rec.table_name, v_field_key);
                    
                    BEGIN
                        EXECUTE v_index_sql;
                        RAISE NOTICE 'Created index % on table %', v_index_name, v_table_rec.table_name;
                    EXCEPTION WHEN OTHERS THEN
                        RAISE NOTICE 'Error creating index % on table %: %', v_index_name, v_table_rec.table_name, SQLERRM;
                    END;
                END IF;
            END IF;
        END LOOP;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 7. SIMPLE SUMMARY VIEW (FIXED)
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
-- 8. SETUP AND USAGE
-- =====================================================

-- Create a simple setup function
CREATE OR REPLACE FUNCTION setup_notion_relations_system()
RETURNS void AS $$
BEGIN
    RAISE NOTICE '🚀 Setting up simplified Notion relations system...';
    
    -- Create the main functions
    RAISE NOTICE '📦 Creating core functions...';
    
    -- Create dynamic relation columns
    PERFORM create_dynamic_relation_columns();
    RAISE NOTICE '✅ Dynamic relation columns created';
    
    -- Create universal relation views
    PERFORM create_universal_relation_views();
    RAISE NOTICE '✅ Universal relation views created';
    
    -- Create automatic indexes
    PERFORM create_automatic_relation_indexes();
    RAISE NOTICE '✅ Automatic indexes created';
    
    RAISE NOTICE '🎉 Setup complete! Your system is ready to use.';
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- USAGE INSTRUCTIONS
-- =====================================================

/*
To set up the system, run:
SELECT setup_notion_relations_system();

To see what relations were detected:
SELECT * FROM auto_detect_all_notion_relations();

To analyze relationship patterns:
SELECT * FROM analyze_relationship_patterns();

To get a summary of all relations:
SELECT * FROM notion_all_relations_summary;

To resolve specific relations:
SELECT * FROM smart_resolve_relations('notion_articles', 'author or source');

Example queries after setup:
- SELECT * FROM notion_articles_universal_relations;
- SELECT * FROM notion_books_universal_relations;
*/