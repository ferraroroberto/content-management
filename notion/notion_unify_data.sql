-- SQL to unify Notion editorial data into a consolidated table
-- This consolidates data from notion_editorial table only

-- Drop existing table if it exists
DROP TABLE IF EXISTS public.unified_data;

-- Create the unified_data table using CREATE TABLE AS SELECT
CREATE TABLE public.unified_data AS
SELECT
    -- Id field (primary key)
    ne.notion_id,
    
    -- Notion reference fields
    ne.notion_data_jsonb,

    -- date field
    ne.date,
    ne.day,

    -- Instagram fields (ig)
    ne.link_ig,
    ne.link_ig_v_,
    ne.text_ig,
    ne.thread_ig,
    ne.repost_ig,
    ne.follow_ig,
    ne.likes_ig,
    ne.likend_ig,
    ne.cmmt_ig,
    ne.reshar_ig,
    ne.result_ig,
    
    -- Twitter fields (tw)
    ne.link_tw,
    ne.link_tw_v_,
    ne.text_tw,
    ne.thread_tw,
    ne.repost_tw,
    ne.follow_tw,
    ne.likes_tw,
    ne.likend_tw,
    ne.cmmt_tw,
    ne.reshar_tw,
    ne.result_tw,
    
    -- LinkedIn fields (li)
    ne.link_li,
    ne.link_li_v_,
    ne.thread_li,
    ne.repost_li,
    ne.check_li,
    ne.follow_li,
    ne.likes_li,
    ne.likend_li,
    ne.cmmt_li,
    ne.reshar_li,
    ne.result_li,
    
    -- Threads fields (th)
    ne.link_th,
    ne.text_th,
    ne.thread_th,
    ne.repost_th,
    ne.follow_th,
    ne.likes_th,
    ne.likend_th,
    ne.cmmt_th,
    ne.reshar_th,
    ne.result_th,
    
    -- Substack fields (sb)
    ne.link_sb,
    ne.text_sb,
    ne.thread_sb,
    ne.repost_sb,
    ne.follow_sb,
    ne.likes_sb,
    ne.likend_sb,
    ne.cmmt_sb,
    ne.reshar_sb,
    ne.result_sb
    
FROM public.notion_editorial ne
WHERE ne.date IS NOT NULL and ne.date <= current_date
ORDER BY ne.date desc;

-- Add primary key constraint on notion_id
ALTER TABLE public.unified_data ADD CONSTRAINT pk_unified_data_notion_id PRIMARY KEY (notion_id);

-- Add table comment
COMMENT ON TABLE public.unified_data IS 'Unified data from Notion editorial table, consolidated by date';

-- Create index on date for better performance
CREATE INDEX IF NOT EXISTS idx_unified_data_date ON public.unified_data USING btree (date);

-- Create index on notion_id for reference lookups
CREATE INDEX IF NOT EXISTS idx_unified_data_notion_id ON public.unified_data USING btree (notion_id);
