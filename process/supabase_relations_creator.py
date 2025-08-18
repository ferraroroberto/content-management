#!/usr/bin/env python3
"""
supabase_relations_creator.py

Creates relational structure in Supabase from Notion database relations.
Follows project automation standards with logging and configuration management.
"""

import json
import os
import pandas as pd
import logging
from pathlib import Path
import sys
import psycopg2
import psycopg2.extras
import argparse
from dotenv import load_dotenv
from typing import Dict, List, Any, Optional, Tuple, Set

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger - will use existing logger if available
logger = logging.getLogger("supabase_relations_creator")
if not logger.handlers:
    # Only set up if no handlers exist (i.e., not already configured)
    logger = setup_logger("supabase_relations_creator", file_logging=False)

def apply_table_policies(connection, table_name):
    """
    Apply Row Level Security (RLS) policies to a newly created table.
    
    Args:
        connection: Database connection
        table_name (str): Name of the table to apply policies to
    """
    try:
        # First, drop any existing policies to avoid conflicts
        drop_policies_sql = f"""
        DROP POLICY IF EXISTS anon_select_all ON public."{table_name}";
        DROP POLICY IF EXISTS anon_insert_all ON public."{table_name}";
        DROP POLICY IF EXISTS anon_update_all ON public."{table_name}";
        DROP POLICY IF EXISTS anon_delete_all ON public."{table_name}";
        """
        
        with connection.cursor() as cursor:
            cursor.execute(drop_policies_sql)
        
        # Enable RLS and create new policies
        policies_sql = f"""
        -- Enable RLS on the table
        ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY;
        
        -- Create policies for anon access
        CREATE POLICY anon_select_all ON public."{table_name}" FOR SELECT TO anon USING (true);
        CREATE POLICY anon_insert_all ON public."{table_name}" FOR INSERT TO anon WITH CHECK (true);
        CREATE POLICY anon_update_all ON public."{table_name}" FOR UPDATE TO anon USING (true) WITH CHECK (true);
        CREATE POLICY anon_delete_all ON public."{table_name}" FOR DELETE TO anon USING (true);
        """
        
        with connection.cursor() as cursor:
            cursor.execute(policies_sql)
        
        logger.debug(f"🔒 Applied RLS policies to table {table_name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error applying policies to table {table_name}: {e}")
        return False

def load_db_config(environment="cloud"):
    """
    Load database configuration from environment variables.
    
    Args:
        environment (str): The environment to use, either 'local' or 'cloud'
    """
    logger.debug(f"📂 Loading database configuration for {environment} environment")
    
    # Load environment variables from .env file
    load_dotenv()
    
    env_suffix = f"_{environment}"
    
    db_config = {
        "user": os.getenv(f"db_user{env_suffix}"),
        "password": os.getenv(f"db_password{env_suffix}"),
        "host": os.getenv(f"db_host{env_suffix}"),
        "port": os.getenv(f"db_port{env_suffix}"),
        "dbname": os.getenv(f"db_name{env_suffix}")
    }
    
    # Check if all required configuration exists
    missing_keys = [k for k, v in db_config.items() if not v]
    if missing_keys:
        logger.error(f"❌ Database configuration missing: {', '.join(missing_keys)}")
        return None
            
    logger.info(f"✅ Database configuration loaded successfully for {environment} environment")
    return db_config

def get_db_connection(db_config=None, environment="cloud"):
    """
    Get a PostgreSQL database connection.
    
    Args:
        db_config (dict, optional): Database configuration parameters
        environment (str, optional): The environment to use if db_config is None
    """
    if db_config is None:
        db_config = load_db_config(environment)
        
    if not db_config:
        return None
        
    try:
        connection = psycopg2.connect(
            user=db_config.get("user"),
            password=db_config.get("password"),
            host=db_config.get("host"),
            port=db_config.get("port"),
            dbname=db_config.get("dbname")
        )
        connection.autocommit = True
        logger.info("✅ Connected to database successfully")
        return connection
    except Exception as e:
        logger.error(f"❌ Error connecting to database: {e}")
        return None

def load_notion_data():
    """
    Load Notion database list and relations data from JSON files.
    
    Returns:
        tuple: (database_list, relations_data) or (None, None) if error
    """
    try:
        # Load database list
        db_list_path = Path(__file__).parent.parent / "notion" / "notion_database_list.json"
        with open(db_list_path, 'r', encoding='utf-8') as f:
            database_list = json.load(f)
        logger.info(f"✅ Loaded {len(database_list)} databases from notion_database_list.json")
        
        # Load relations data
        relations_path = Path(__file__).parent.parent / "notion" / "notion_database_relations.json"
        with open(relations_path, 'r', encoding='utf-8') as f:
            relations_data = json.load(f)
        logger.info(f"✅ Loaded {len(relations_data)} relation configurations from notion_database_relations.json")
        
        return database_list, relations_data
        
    except Exception as e:
        logger.error(f"❌ Error loading Notion data: {e}")
        return None, None

def create_database_mapping(database_list):
    """
    Create a mapping from database ID to database info.
    
    Args:
        database_list (list): List of database configurations
        
    Returns:
        dict: Mapping of database ID to database info
    """
    mapping = {}
    for db in database_list:
        mapping[db['id']] = {
            'name': db['name'],
            'supabase_table': db['supabase_table'],
            'replication': db['replication']
        }
    
    logger.debug(f"📋 Created database mapping with {len(mapping)} entries")
    logger.debug(f"   Database IDs: {list(mapping.keys())}")
    
    return mapping

def drop_relations_table(connection, table_name="notion_relations_master"):
    """
    Drop the relations master table if it exists.
    
    Args:
        connection: Database connection
        table_name (str): Name of the table to drop
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
        logger.info(f"🗑️  Dropped table {table_name}")
        return True
    except Exception as e:
        logger.error(f"❌ Error dropping table {table_name}: {e}")
        return False

def create_master_relations_table(connection, table_name="notion_relations_master"):
    """
    Create the master relations table.
    
    Args:
        connection: Database connection
        table_name (str): Name of the table to create
    """
    try:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id SERIAL PRIMARY KEY,
            origin_database_id VARCHAR(255),
            origin_database_name VARCHAR(255),
            origin_supabase_table VARCHAR(255),
            relation_field_name VARCHAR(255),
            related_database_id VARCHAR(255),
            related_database_name VARCHAR(255),
            related_supabase_table VARCHAR(255),
            junction_table_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_{table_name}_origin ON "{table_name}"(origin_supabase_table);
        CREATE INDEX IF NOT EXISTS idx_{table_name}_related ON "{table_name}"(related_supabase_table);
        CREATE INDEX IF NOT EXISTS idx_{table_name}_junction ON "{table_name}"(junction_table_name);
        """
        
        with connection.cursor() as cursor:
            cursor.execute(create_sql)
        
        # Apply RLS policies after table creation
        apply_table_policies(connection, table_name)
        
        logger.info(f"✅ Created master relations table {table_name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating master relations table: {e}")
        return False

def populate_master_relations_table(connection, database_list, relations_data, table_name="notion_relations_master"):
    """
    Populate the master relations table with data from JSON files.
    
    Args:
        connection: Database connection
        database_list (list): List of database configurations
        relations_data (list): List of relation configurations
        table_name (str): Name of the master relations table
    """
    try:
        # Create database mapping
        db_mapping = create_database_mapping(database_list)
        
        # Prepare data for insertion
        relations_to_insert = []
        
        logger.debug(f"🔍 Processing {len(relations_data)} relation configurations")
        
        for relation_config in relations_data:
            origin_db_id = relation_config['origin_database_id']
            origin_info = db_mapping.get(origin_db_id, {})
            
            if not origin_info:
                logger.warning(f"⚠️ Origin database ID '{origin_db_id}' not found in database list!")
                logger.debug(f"   Available database IDs: {list(db_mapping.keys())}")
                logger.debug(f"   This ID might be malformed or missing from notion_database_list.json")
            
            logger.debug(f"📋 Processing origin database: {origin_db_id} -> {origin_info.get('name', 'Unknown')} ({origin_info.get('supabase_table', 'unknown')})")
            logger.debug(f"   Relations count: {len(relation_config['relations'])}")
            
            for relation in relation_config['relations']:
                related_db_id = relation['related_database_id']
                related_info = db_mapping.get(related_db_id, {})
                
                if not related_info:
                    logger.warning(f"⚠️ Related database ID '{related_db_id}' not found in database list!")
                    logger.debug(f"   Available database IDs: {list(db_mapping.keys())}")
                    logger.debug(f"   This ID might be malformed or missing from notion_database_list.json")
                
                # Generate junction table name
                if origin_info.get('supabase_table') == related_info.get('supabase_table'):
                    # Self-referential
                    junction_name = f"{origin_info.get('supabase_table', 'unknown')}_relations"
                else:
                    # Regular junction
                    tables = sorted([
                        origin_info.get('supabase_table', 'unknown'),
                        related_info.get('supabase_table', 'unknown')
                    ])
                    junction_name = f"{tables[0]}_to_{tables[1]}"
                
                relation_record = {
                    'origin_database_id': origin_db_id,
                    'origin_database_name': origin_info.get('name', 'Unknown'),
                    'origin_supabase_table': origin_info.get('supabase_table', 'unknown'),
                    'relation_field_name': relation['field_name'],
                    'related_database_id': related_db_id,
                    'related_database_name': related_info.get('name', 'Unknown'),
                    'related_supabase_table': related_info.get('supabase_table', 'unknown'),
                    'junction_table_name': junction_name
                }
                
                logger.debug(f"   🔗 {relation['field_name']} -> {related_info.get('name', 'Unknown')} ({related_info.get('supabase_table', 'unknown')}) [Junction: {junction_name}]")
                
                relations_to_insert.append(relation_record)
        
        logger.info(f"📊 Total individual relations to insert: {len(relations_to_insert)}")
        
        # Insert data
        if relations_to_insert:
            columns = list(relations_to_insert[0].keys())
            columns_str = ', '.join([f'"{col}"' for col in columns])
            placeholders = ', '.join(['%s'] * len(columns))
            
            insert_sql = f"""
                INSERT INTO "{table_name}" ({columns_str})
                VALUES ({placeholders})
            """
            
            with connection.cursor() as cursor:
                for record in relations_to_insert:
                    values = [record[col] for col in columns]
                    cursor.execute(insert_sql, values)
            
            logger.info(f"✅ Inserted {len(relations_to_insert)} relations into master table")
        else:
            logger.warning("⚠️ No relations to insert")
            
        return True
        
    except Exception as e:
        logger.error(f"❌ Error populating master relations table: {e}")
        return False

def drop_junction_tables(connection, relations_data, database_list, deduplicate=True):
    """
    Drop all existing junction tables.
    
    Args:
        connection: Database connection
        relations_data (list): List of relation configurations
        database_list (list): List of database configurations
        deduplicate (bool): If True, expect deduplicated tables. If False, expect separate tables for each direction.
    """
    try:
        db_mapping = create_database_mapping(database_list)
        junction_tables = set()
        
        logger.debug(f"🔍 Collecting junction table names (deduplicate: {deduplicate})...")
        
        # Collect all junction table names
        for relation_config in relations_data:
            origin_db_id = relation_config['origin_database_id']
            origin_info = db_mapping.get(origin_db_id, {})
            
            for relation in relation_config['relations']:
                related_db_id = relation['related_database_id']
                related_info = db_mapping.get(related_db_id, {})
                
                if origin_info.get('supabase_table') == related_info.get('supabase_table'):
                    junction_name = f"{origin_info.get('supabase_table', 'unknown')}_relations"
                    junction_tables.add(junction_name)
                    logger.debug(f"   📋 Junction table: {junction_name}")
                else:
                    if deduplicate:
                        # Original behavior: one table per relationship direction
                        tables = sorted([
                            origin_info.get('supabase_table', 'unknown'),
                            related_info.get('supabase_table', 'unknown')
                        ])
                        junction_name = f"{tables[0]}_to_{tables[1]}"
                        junction_tables.add(junction_name)
                        logger.debug(f"   📋 Junction table: {junction_name}")
                    else:
                        # New behavior: separate tables for each direction
                        origin_table = origin_info.get('supabase_table', 'unknown')
                        related_table = related_info.get('supabase_table', 'unknown')
                        
                        junction_name_forward = f"{origin_table}_to_{related_table}"
                        junction_name_reverse = f"{related_table}_to_{origin_table}"
                        
                        junction_tables.add(junction_name_forward)
                        junction_tables.add(junction_name_reverse)
                        logger.debug(f"   📋 Junction tables: {junction_name_forward}, {junction_name_reverse}")
        
        logger.info(f"📊 Found {len(junction_tables)} unique junction tables")
        
        # Drop each junction table
        with connection.cursor() as cursor:
            for table_name in junction_tables:
                cursor.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
                logger.info(f"🗑️  Dropped junction table {table_name}")
        
        logger.info(f"✅ Dropped {len(junction_tables)} junction tables")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error dropping junction tables: {e}")
        return False

def create_junction_tables(connection, relations_data, database_list, deduplicate=True):
    """
    Create all junction tables based on relations data.
    
    Args:
        connection: Database connection
        relations_data (list): List of relation configurations
        database_list (list): List of database configurations
        deduplicate (bool): If True, create one table per relationship direction. If False, create separate tables for each direction.
    """
    try:
        db_mapping = create_database_mapping(database_list)
        created_tables = []
        
        logger.debug(f"🔍 Creating junction tables (deduplicate: {deduplicate})...")
        
        for relation_config in relations_data:
            origin_db_id = relation_config['origin_database_id']
            origin_info = db_mapping.get(origin_db_id, {})
            
            for relation in relation_config['relations']:
                related_db_id = relation['related_database_id']
                related_info = db_mapping.get(related_db_id, {})
                
                origin_table = origin_info.get('supabase_table', 'unknown')
                related_table = related_info.get('supabase_table', 'unknown')
                
                if origin_table == related_table:
                    # Self-referential table
                    junction_name = f"{origin_table}_relations"
                    create_sql = f"""
                    CREATE TABLE IF NOT EXISTS "{junction_name}" (
                        id SERIAL PRIMARY KEY,
                        source_notion_id VARCHAR(255) NOT NULL,
                        target_notion_id VARCHAR(255) NOT NULL,
                        relation_field_name VARCHAR(255) NOT NULL,
                        UNIQUE(source_notion_id, target_notion_id, relation_field_name)
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_{junction_name}_source ON "{junction_name}"(source_notion_id);
                    CREATE INDEX IF NOT EXISTS idx_{junction_name}_target ON "{junction_name}"(target_notion_id);
                    CREATE INDEX IF NOT EXISTS idx_{junction_name}_field ON "{junction_name}"(relation_field_name);
                    """
                else:
                    if deduplicate:
                        # Create one table per relationship direction (original behavior)
                        tables = sorted([origin_table, related_table])
                        junction_name = f"{tables[0]}_to_{tables[1]}"
                        
                        create_sql = f"""
                        CREATE TABLE IF NOT EXISTS "{junction_name}" (
                            id SERIAL PRIMARY KEY,
                            {origin_table}_notion_id VARCHAR(255) NOT NULL,
                            relation_field_name VARCHAR(255) NOT NULL,
                            {related_table}_notion_id VARCHAR(255) NOT NULL,
                            UNIQUE({origin_table}_notion_id, relation_field_name, {related_table}_notion_id)
                        );
                        
                        CREATE INDEX IF NOT EXISTS idx_{junction_name}_origin ON "{junction_name}"({origin_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name}_related ON "{junction_name}"({related_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name}_field ON "{junction_name}"(relation_field_name);
                        """
                    else:
                        # Create separate tables for each direction
                        junction_name_forward = f"{origin_table}_to_{related_table}"
                        junction_name_reverse = f"{related_table}_to_{origin_table}"
                        
                        # Forward direction table
                        create_sql_forward = f"""
                        CREATE TABLE IF NOT EXISTS "{junction_name_forward}" (
                            id SERIAL PRIMARY KEY,
                            {origin_table}_notion_id VARCHAR(255) NOT NULL,
                            relation_field_name VARCHAR(255) NOT NULL,
                            {related_table}_notion_id VARCHAR(255) NOT NULL,
                            UNIQUE({origin_table}_notion_id, relation_field_name, {related_table}_notion_id)
                        );
                        
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_forward}_origin ON "{junction_name_forward}"({origin_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_forward}_related ON "{junction_name_forward}"({related_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_forward}_field ON "{junction_name_forward}"(relation_field_name);
                        """
                        
                        # Reverse direction table
                        create_sql_reverse = f"""
                        CREATE TABLE IF NOT EXISTS "{junction_name_reverse}" (
                            id SERIAL PRIMARY KEY,
                            {related_table}_notion_id VARCHAR(255) NOT NULL,
                            relation_field_name VARCHAR(255) NOT NULL,
                            {origin_table}_notion_id VARCHAR(255) NOT NULL,
                            UNIQUE({related_table}_notion_id, relation_field_name, {origin_table}_notion_id)
                        );
                        
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_reverse}_origin ON "{junction_name_reverse}"({related_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_reverse}_related ON "{junction_name_reverse}"({origin_table}_notion_id);
                        CREATE INDEX IF NOT EXISTS idx_{junction_name_reverse}_field ON "{junction_name_reverse}"(relation_field_name);
                        """
                        
                        # Create both tables
                        with connection.cursor() as cursor:
                            cursor.execute(create_sql_forward)
                            cursor.execute(create_sql_reverse)
                        
                        # Apply RLS policies after table creation
                        apply_table_policies(connection, junction_name_forward)
                        apply_table_policies(connection, junction_name_reverse)
                        
                        if junction_name_forward not in created_tables:
                            created_tables.append(junction_name_forward)
                            logger.info(f"✅ Created junction table {junction_name_forward} - {origin_table} -> {related_table} via '{relation['field_name']}'")
                        
                        if junction_name_reverse not in created_tables:
                            created_tables.append(junction_name_reverse)
                            logger.info(f"✅ Created junction table {junction_name_reverse} - {related_table} -> {origin_table} via '{relation['field_name']}'")
                        
                        continue  # Skip the single table creation below
                
                with connection.cursor() as cursor:
                    cursor.execute(create_sql)
                
                # Apply RLS policies after table creation
                apply_table_policies(connection, junction_name)
                
                if junction_name not in created_tables:
                    created_tables.append(junction_name)
                    logger.info(f"✅ Created junction table {junction_name} - {origin_table} <-> {related_table} via '{relation['field_name']}'")
        
        logger.info(f"✅ Created {len(created_tables)} junction tables")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating junction tables: {e}")
        return False

def extract_relations_from_source_tables(connection, relations_data, database_list, deduplicate=True):
    """
    Extract relations from source tables using SQL for bulk operations.
    
    Args:
        connection: Database connection
        relations_data: Relations configuration data
        database_list: List of database information
        deduplicate (bool): If True, expect deduplicated tables. If False, expect separate tables for each direction.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        db_mapping = create_database_mapping(database_list)
        total_relations = 0
        
        for relation_config in relations_data:
            origin_db_id = relation_config['origin_database_id']
            origin_info = db_mapping.get(origin_db_id, {})
            origin_table = origin_info.get('supabase_table', 'unknown')
            
            if not origin_table or origin_table == 'unknown':
                logger.warning(f"⚠️ Skipping unknown origin table for database {origin_db_id}")
                continue
            
            logger.info(f"🔍 Processing relations for {origin_table}")
            
            # Check if source table exists
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    );
                """, (origin_table,))
                
                if not cursor.fetchone()[0]:
                    logger.warning(f"⚠️ Source table {origin_table} does not exist, skipping")
                    continue
            
            # Get record count for progress tracking
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT COUNT(*) FROM "{origin_table}" WHERE notion_data_jsonb IS NOT NULL')
                record_count = cursor.fetchone()[0]
            
            if record_count == 0:
                logger.info(f"📭 No records with JSONB data found in {origin_table}")
                continue
            
            logger.info(f"📦 Processing {record_count} records from {origin_table}")
            
            # Process each relation field using SQL
            for relation in relation_config['relations']:
                field_name = relation['field_name']
                related_db_id = relation['related_database_id']
                related_info = db_mapping.get(related_db_id, {})
                related_table = related_info.get('supabase_table', 'unknown')
                
                if not related_table or related_table == 'unknown':
                    logger.warning(f"⚠️ Skipping unknown related table for database {related_db_id}")
                    continue
                
                # Determine junction table name
                if origin_table == related_table:
                    junction_name = f"{origin_table}_relations"
                    # Use SQL to extract and insert all relations at once
                    insert_sql = f"""
                    INSERT INTO "{junction_name}" (source_notion_id, target_notion_id, relation_field_name)
                    SELECT 
                        notion_id as source_notion_id,
                        jsonb_array_elements_text(notion_data_jsonb->%s) as target_notion_id,
                        %s as relation_field_name
                    FROM "{origin_table}"
                    WHERE notion_data_jsonb->%s IS NOT NULL 
                    AND jsonb_typeof(notion_data_jsonb->%s) = 'array'
                    AND jsonb_array_length(notion_data_jsonb->%s) > 0
                    ON CONFLICT (source_notion_id, target_notion_id, relation_field_name) DO NOTHING
                    """
                    
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(insert_sql, (field_name, field_name, field_name, field_name, field_name))
                            relations_inserted = cursor.rowcount
                            total_relations += relations_inserted
                            logger.info(f"✅ Inserted {relations_inserted} relations for {field_name} in {junction_name}")
                    except Exception as e:
                        logger.error(f"❌ Error inserting relations for {field_name} in {junction_name}: {e}")
                        continue
                        
                else:
                    if deduplicate:
                        # Original behavior: one table per relationship direction
                        tables = sorted([origin_table, related_table])
                        junction_name = f"{tables[0]}_to_{tables[1]}"
                        
                        # Use SQL to extract and insert all relations at once
                        insert_sql = f"""
                        INSERT INTO "{junction_name}" ({origin_table}_notion_id, relation_field_name, {related_table}_notion_id)
                        SELECT 
                            notion_id as {origin_table}_notion_id,
                            %s as relation_field_name,
                            jsonb_array_elements_text(notion_data_jsonb->%s) as {related_table}_notion_id
                        FROM "{origin_table}"
                        WHERE notion_data_jsonb->%s IS NOT NULL 
                        AND jsonb_typeof(notion_data_jsonb->%s) = 'array'
                        AND jsonb_array_length(notion_data_jsonb->%s) > 0
                        ON CONFLICT ({origin_table}_notion_id, relation_field_name, {related_table}_notion_id) DO NOTHING
                        """
                        
                        try:
                            with connection.cursor() as cursor:
                                cursor.execute(insert_sql, (field_name, field_name, field_name, field_name, field_name))
                                relations_inserted = cursor.rowcount
                                total_relations += relations_inserted
                                logger.info(f"✅ Inserted {relations_inserted} relations for {field_name} in {junction_name}")
                        except Exception as e:
                            logger.error(f"❌ Error inserting relations for {field_name} in {junction_name}: {e}")
                            continue
                    else:
                        # New behavior: separate tables for each direction
                        junction_name_forward = f"{origin_table}_to_{related_table}"
                        
                        # Use SQL to extract and insert all relations at once
                        insert_sql = f"""
                        INSERT INTO "{junction_name_forward}" ({origin_table}_notion_id, relation_field_name, {related_table}_notion_id)
                        SELECT 
                            notion_id as {origin_table}_notion_id,
                            %s as relation_field_name,
                            jsonb_array_elements_text(notion_data_jsonb->%s) as {related_table}_notion_id
                        FROM "{origin_table}"
                        WHERE notion_data_jsonb->%s IS NOT NULL 
                        AND jsonb_typeof(notion_data_jsonb->%s) = 'array'
                        AND jsonb_array_length(notion_data_jsonb->%s) > 0
                        ON CONFLICT ({origin_table}_notion_id, relation_field_name, {related_table}_notion_id) DO NOTHING
                        """
                        
                        try:
                            with connection.cursor() as cursor:
                                cursor.execute(insert_sql, (field_name, field_name, field_name, field_name, field_name))
                                relations_inserted = cursor.rowcount
                                total_relations += relations_inserted
                                logger.info(f"✅ Inserted {relations_inserted} relations for {field_name} in {junction_name_forward}")
                        except Exception as e:
                            logger.error(f"❌ Error inserting relations for {field_name} in {junction_name_forward}: {e}")
                            continue
        
        logger.info(f"✅ Extracted and inserted {total_relations} relations total")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error extracting relations from source tables: {e}")
        return False



def create_all_relations(environment="cloud", db_config=None, deduplicate=True):
    """
    Create all relations: master table, junction tables, and populate with data.
    
    Args:
        environment (str): Database environment to use
        db_config (dict, optional): Database configuration
        deduplicate (bool): If True, create one table per relationship direction. If False, create separate tables for each direction.
    """
    logger.info(f"🚀 Starting relations creation process (deduplicate: {deduplicate})")
    
    # Get database connection
    connection = get_db_connection(db_config, environment)
    if not connection:
        return False
    
    try:
        # Step 1: Load Notion data
        database_list, relations_data = load_notion_data()
        if not database_list or not relations_data:
            return False
        
        # Step 2: Drop existing relations table
        if not drop_relations_table(connection):
            return False
        
        # Step 3: Drop existing junction tables
        if not drop_junction_tables(connection, relations_data, database_list, deduplicate):
            return False
        
        # Step 4: Create master relations table
        if not create_master_relations_table(connection):
            return False
        
        # Step 5: Populate master relations table
        if not populate_master_relations_table(connection, database_list, relations_data):
            return False
        
        # Step 6: Create junction tables
        if not create_junction_tables(connection, relations_data, database_list, deduplicate):
            return False
        
        # Step 7: Extract and populate relations data
        if not extract_relations_from_source_tables(connection, relations_data, database_list, deduplicate):
            return False
        
        logger.info("✅ Relations creation process completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error in relations creation process: {e}")
        return False
    finally:
        if connection:
            connection.close()
            logger.debug("Database connection closed")

def drop_all_tables(environment="cloud", deduplicate=True):
    """
    Drop all tables in the Supabase database.
    
    Args:
        environment (str): Database environment to use
        deduplicate (bool): If True, expect deduplicated tables. If False, expect separate tables for each direction.
    """
    logger.info(f"🚀 Starting drop_all_tables process (deduplicate: {deduplicate})")
    
    db_config = load_db_config(environment)
    if not db_config:
        logger.error("❌ Failed to load database configuration.")
        return False
        
    connection = get_db_connection(db_config, environment)
    if not connection:
        logger.error("❌ Failed to get database connection.")
        return False
        
    try:
        # Load Notion data to know which tables to drop
        database_list, relations_data = load_notion_data()
        if not database_list or not relations_data:
            logger.warning("⚠️ Could not load Notion data, attempting to drop known tables")
            # Try to drop the master relations table anyway
            if not drop_relations_table(connection):
                logger.warning("⚠️ Could not drop notion_relations_master table.")
            return True
        
        # Drop master relations table
        if not drop_relations_table(connection):
            logger.warning("⚠️ Could not drop notion_relations_master table.")
        
        # Drop all junction tables
        if not drop_junction_tables(connection, relations_data, database_list, deduplicate):
            logger.warning("⚠️ Could not drop junction tables.")
        

        return True
        
    except Exception as e:
        logger.error(f"❌ Error dropping all tables: {e}")
        return False
    finally:
        if connection:
            connection.close()
            logger.debug("Database connection closed")

def main():
    """Main function for testing the module independently."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Create Notion relations in Supabase database")
    parser.add_argument("--environment", choices=["local", "cloud"], default="cloud",
                        help="Database environment to use (default: cloud)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without executing")
    parser.add_argument("--drop-all", action="store_true", help="Drop all tables without recreating them")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--de-duplicate", action="store_true", help="Deduplicate junction tables (default: False)")
    args = parser.parse_args()
    
    # Configure debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)  # Also set the specific logger level
        
        # Update all handler levels to DEBUG as well
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)
        
        logger.debug("🔍 DEBUG mode enabled - showing detailed information")
    
    # Configure logger
    logger.info(f"🚀 Starting Relations Creator (Environment: {args.environment})")
    
    if args.drop_all:
        logger.info("🗑️  DROP ALL MODE - Dropping all tables without recreating them")
        success = drop_all_tables(args.environment, deduplicate=args.de_duplicate)
        if success:
            logger.info("✅ All tables dropped successfully")
        else:
            logger.error("❌ Failed to drop all tables")
            sys.exit(1)
        return
    
    if args.dry_run:
        logger.info(f"🔍 DRY RUN MODE - No changes will be made to database (deduplicate: {args.de_duplicate})")
        # Load and display what would be created
        database_list, relations_data = load_notion_data()
        if database_list and relations_data:
            logger.info(f"📊 Would create relations for {len(database_list)} databases")
            logger.info(f"🔗 Would create junction tables for {len(relations_data)} relation configurations")
            
            # Show detailed breakdown in DEBUG mode only
            if args.debug:
                total_relations = 0
                junction_tables = set()
                db_mapping = create_database_mapping(database_list)
                
                logger.debug("📋 DETAILED BREAKDOWN:")
                for relation_config in relations_data:
                    origin_db_id = relation_config['origin_database_id']
                    origin_info = db_mapping.get(origin_db_id, {})
                    origin_name = origin_info.get('name', 'Unknown')
                    origin_table = origin_info.get('supabase_table', 'unknown')
                    
                    logger.debug(f"  📁 {origin_name} ({origin_table})")
                    logger.debug(f"     Relations: {len(relation_config['relations'])}")
                    
                    for relation in relation_config['relations']:
                        related_db_id = relation['related_database_id']
                        related_info = db_mapping.get(related_db_id, {})
                        related_name = related_info.get('name', 'Unknown')
                        related_table = related_info.get('supabase_table', 'unknown')
                        
                        # Determine junction table name
                        if origin_table == related_table:
                            junction_name = f"{origin_table}_relations"
                            junction_tables.add(junction_name)
                        else:
                            if args.de_duplicate:
                                # Original behavior: one table per relationship direction
                                tables = sorted([origin_table, related_table])
                                junction_name = f"{tables[0]}_to_{tables[1]}"
                                junction_tables.add(junction_name)
                            else:
                                # New behavior: separate tables for each direction
                                junction_name_forward = f"{origin_table}_to_{related_table}"
                                junction_name_reverse = f"{related_table}_to_{origin_table}"
                                junction_tables.add(junction_name_forward)
                                junction_tables.add(junction_name_reverse)
                        
                        total_relations += 1
                        
                        if origin_table == related_table:
                            logger.debug(f"       🔗 '{relation['field_name']}' -> {related_name} ({related_table}) [Junction: {junction_name}]")
                        else:
                            if args.de_duplicate:
                                logger.debug(f"       🔗 '{relation['field_name']}' -> {related_name} ({related_table}) [Junction: {junction_name}]")
                            else:
                                logger.debug(f"       🔗 '{relation['field_name']}' -> {related_name} ({related_table}) [Junctions: {origin_table}_to_{related_table}, {related_table}_to_{origin_table}]")
                
                logger.debug(f"📊 SUMMARY:")
                logger.debug(f"   Total individual relations: {total_relations}")
                logger.debug(f"   Unique junction tables: {len(junction_tables)}")
                logger.debug(f"   Junction tables: {', '.join(sorted(junction_tables))}")
                
                # Show problematic IDs
                logger.debug("🔍 CHECKING FOR PROBLEMATIC DATABASE IDs:")
                problematic_ids = set()
                for relation_config in relations_data:
                    origin_db_id = relation_config['origin_database_id']
                    if origin_db_id not in db_mapping:
                        problematic_ids.add(origin_db_id)
                    
                    for relation in relation_config['relations']:
                        related_db_id = relation['related_database_id']
                        if related_db_id not in db_mapping:
                            problematic_ids.add(related_db_id)
                
                if problematic_ids:
                    logger.warning(f"⚠️ Found {len(problematic_ids)} problematic database IDs:")
                    for pid in problematic_ids:
                        logger.warning(f"   ❌ '{pid}' - not found in notion_database_list.json")
                else:
                    logger.debug("✅ All database IDs found in database list")
            
        return
    
    # Execute relations creation
    success = create_all_relations(args.environment, deduplicate=args.de_duplicate)
    
    if success:
        logger.info("✅ Relations creation completed successfully")
    else:
        logger.error("❌ Relations creation failed")
        sys.exit(1)

if __name__ == "__main__":
    main()