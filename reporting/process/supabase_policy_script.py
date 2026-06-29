#!/usr/bin/env python3
"""
supabase_policy_script.py

Applies Row Level Security (RLS) policies to all existing tables in Supabase.
Follows project automation standards with logging and configuration management.
"""

import json
import logging
from pathlib import Path
import sys
import psycopg2
import psycopg2.extras
from psycopg2 import sql
import argparse
from typing import Dict, List, Any, Optional, Tuple, Set

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent.parent))
from config.logger_config import setup_logger
# Single-source DB helpers — canonical defs live in supabase_uploader.
from reporting.process.supabase_uploader import load_db_config, get_db_connection

# Set up logger - will use existing logger if available
logger = logging.getLogger("supabase_policy_script")
if not logger.handlers:
    # Only set up if no handlers exist (i.e., not already configured)
    logger = setup_logger("supabase_policy_script", file_logging=False)

def get_all_tables(connection):
    """
    Get all tables in the public schema.
    
    Args:
        connection: Database connection
        
    Returns:
        list: List of table names
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT tablename 
                FROM pg_tables 
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)
            tables = [row[0] for row in cursor.fetchall()]
        
        logger.info(f"📋 Found {len(tables)} tables in public schema")
        return tables
        
    except Exception as e:
        logger.error(f"❌ Error getting tables: {e}")
        return []

def get_all_views(connection):
    """
    Get all views in the public schema.
    
    Args:
        connection: Database connection
        
    Returns:
        list: List of view names
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT viewname 
                FROM pg_views 
                WHERE schemaname = 'public'
                ORDER BY viewname
                """
            )
            views = [row[0] for row in cursor.fetchall()]
        
        logger.info(f"📋 Found {len(views)} views in public schema")
        return views
        
    except Exception as e:
        logger.error(f"❌ Error getting views: {e}")
        return []

def check_table_policies(connection, table_name):
    """
    Check if a table already has the required policies.
    
    Args:
        connection: Database connection
        table_name (str): Name of the table to check
        
    Returns:
        dict: Policy status information
    """
    try:
        with connection.cursor() as cursor:
            # Check if RLS is enabled
            cursor.execute("""
                SELECT rowsecurity 
                FROM pg_tables 
                WHERE schemaname = 'public' AND tablename = %s
            """, (table_name,))
            
            rls_result = cursor.fetchone()
            rls_enabled = rls_result[0] if rls_result else False
            
            # Check existing policies
            cursor.execute("""
                SELECT policyname 
                FROM pg_policies 
                WHERE schemaname = 'public' AND tablename = %s
            """, (table_name,))
            
            existing_policies = [row[0] for row in cursor.fetchall()]
            
            required_policies = ['anon_select_all', 'anon_insert_all', 'anon_update_all', 'anon_delete_all']
            missing_policies = [p for p in required_policies if p not in existing_policies]
            
            return {
                'rls_enabled': rls_enabled,
                'existing_policies': existing_policies,
                'missing_policies': missing_policies,
                'has_all_policies': len(missing_policies) == 0
            }
            
    except Exception as e:
        logger.error(f"❌ Error checking policies for table {table_name}: {e}")
        return None

def apply_table_policies(connection, table_name, force=False):
    """
    Apply Row Level Security (RLS) policies to a table.
    
    Args:
        connection: Database connection
        table_name (str): Name of the table to apply policies to
        force (bool): If True, drop existing policies before creating new ones
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Check current policy status
        policy_status = check_table_policies(connection, table_name)
        if not policy_status:
            return False
        
        if policy_status['has_all_policies'] and not force:
            logger.debug(f"🔒 Table {table_name} already has all required policies")
            return True
        
        # Drop existing policies if force=True or if some policies exist
        if force or policy_status['existing_policies']:
            drop_policies_sql = f"""
            DROP POLICY IF EXISTS anon_select_all ON public."{table_name}";
            DROP POLICY IF EXISTS anon_insert_all ON public."{table_name}";
            DROP POLICY IF EXISTS anon_update_all ON public."{table_name}";
            DROP POLICY IF EXISTS anon_delete_all ON public."{table_name}";
            """
            
            with connection.cursor() as cursor:
                cursor.execute(drop_policies_sql)
            
            logger.debug(f"🗑️  Dropped existing policies from table {table_name}")
        
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
        
        logger.info(f"🔒 Applied RLS policies to table {table_name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error applying policies to table {table_name}: {e}")
        return False

def apply_policies_to_all_tables(connection, force=False):
    """
    Apply RLS policies to all tables in the public schema.
    
    Args:
        connection: Database connection
        force (bool): If True, drop existing policies before creating new ones
        
    Returns:
        dict: Summary of results
    """
    try:
        tables = get_all_tables(connection)
        if not tables:
            return {'success': False, 'error': 'No tables found'}
        
        results = {
            'total_tables': len(tables),
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'errors': []
        }
        
        logger.info(f"🚀 Starting policy application to {len(tables)} tables (force: {force})")
        
        for table_name in tables:
            try:
                if apply_table_policies(connection, table_name, force=force):
                    results['successful'] += 1
                else:
                    results['failed'] += 1
                    results['errors'].append(f"Failed to apply policies to {table_name}")
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Error with {table_name}: {e}")
                logger.error(f"❌ Unexpected error with table {table_name}: {e}")
        
        logger.info(f"✅ Policy application completed:")
        logger.info(f"   Total tables: {results['total_tables']}")
        logger.info(f"   Successful: {results['successful']}")
        logger.info(f"   Failed: {results['failed']}")
        logger.info(f"   Skipped: {results['skipped']}")
        
        if results['errors']:
            logger.warning(f"⚠️ {len(results['errors'])} errors occurred:")
            for error in results['errors'][:5]:  # Show first 5 errors
                logger.warning(f"   • {error}")
            if len(results['errors']) > 5:
                logger.warning(f"   ... and {len(results['errors']) - 5} more errors")
        
        return results
        
    except Exception as e:
        logger.error(f"❌ Error applying policies to all tables: {e}")
        return {'success': False, 'error': str(e)}

def apply_security_invoker_to_all_views(connection):
    """
    Apply security_invoker = on to all views in the public schema.
    
    This makes views run with the permissions of the querying role so that
    underlying table RLS applies when accessing data through views.
    
    Args:
        connection: Database connection
        
    Returns:
        dict: Summary of results
    """
    try:
        views = get_all_views(connection)
        if not views:
            return {'success': True, 'total_views': 0, 'updated': 0, 'failed': 0}
        
        results = {
            'total_views': len(views),
            'updated': 0,
            'failed': 0,
            'errors': []
        }
        
        logger.info(f"🚀 Applying security_invoker=on to {len(views)} views")
        
        for view_name in views:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL('ALTER VIEW {}.{} SET (security_invoker = on);').format(
                            sql.Identifier('public'),
                            sql.Identifier(view_name)
                        )
                    )
                results['updated'] += 1
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"{view_name}: {e}")
                logger.error(f"❌ Error applying security_invoker to view {view_name}: {e}")
        
        logger.info(
            f"✅ security_invoker application completed: updated={results['updated']} failed={results['failed']}"
        )
        results['success'] = results['failed'] == 0
        return results
    except Exception as e:
        logger.error(f"❌ Error applying security_invoker to all views: {e}")
        return {'success': False, 'error': str(e)}

def dry_run_policy_application(connection):
    """
    Show what policies would be applied without actually applying them.
    
    Args:
        connection: Database connection
        
    Returns:
        dict: Summary of what would be done
    """
    try:
        tables = get_all_tables(connection)
        if not tables:
            return {'success': False, 'error': 'No tables found'}
        
        summary = {
            'total_tables': len(tables),
            'need_rls_enabled': 0,
            'need_policies': 0,
            'already_configured': 0,
            'table_details': []
        }
        
        logger.info(f"🔍 DRY RUN - Analyzing {len(tables)} tables")
        
        for table_name in tables:
            policy_status = check_table_policies(connection, table_name)
            if not policy_status:
                continue
            
            table_detail = {
                'name': table_name,
                'rls_enabled': policy_status['rls_enabled'],
                'existing_policies': policy_status['existing_policies'],
                'missing_policies': policy_status['missing_policies'],
                'action_needed': []
            }
            
            if not policy_status['rls_enabled']:
                table_detail['action_needed'].append('Enable RLS')
                summary['need_rls_enabled'] += 1
            
            if policy_status['missing_policies']:
                table_detail['action_needed'].append(f"Create {len(policy_status['missing_policies'])} policies")
                summary['need_policies'] += 1
            
            if not table_detail['action_needed']:
                summary['already_configured'] += 1
                table_detail['action_needed'].append('No action needed')
            
            summary['table_details'].append(table_detail)
        
        # Display summary
        logger.info(f"📊 DRY RUN SUMMARY:")
        logger.info(f"   Total tables: {summary['total_tables']}")
        logger.info(f"   Need RLS enabled: {summary['need_rls_enabled']}")
        logger.info(f"   Need policies created: {summary['need_policies']}")
        logger.info(f"   Already configured: {summary['already_configured']}")
        
        # Show detailed breakdown for first few tables
        logger.debug("📋 DETAILED BREAKDOWN:")
        for table_detail in summary['table_details'][:10]:  # Show first 10
            action_text = ', '.join(table_detail['action_needed'])
            logger.debug(f"   📁 {table_detail['name']}: {action_text}")
        
        if len(summary['table_details']) > 10:
            logger.debug(f"   ... and {len(summary['table_details']) - 10} more tables")
        
        return summary
        
    except Exception as e:
        logger.error(f"❌ Error in dry run: {e}")
        return {'success': False, 'error': str(e)}

def main():
    """Main function for running the policy script."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Apply RLS policies to all tables in Supabase")
    parser.add_argument("--environment", choices=["local", "cloud"], default="cloud",
                        help="Database environment to use (default: cloud)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")
    parser.add_argument("--force", action="store_true", help="Drop existing policies before creating new ones")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    # Configure debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        
        # Update all handler levels to DEBUG as well
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)
        
        logger.debug("🔍 DEBUG mode enabled - showing detailed information")
    
    # Configure logger
    logger.info(f"🚀 Starting Policy Script (Environment: {args.environment})")
    
    # Get database connection
    connection = get_db_connection(environment=args.environment)
    if not connection:
        logger.error("❌ Failed to get database connection")
        sys.exit(1)
    
    try:
        if args.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made to database")
            result = dry_run_policy_application(connection)
            if not result.get('success', True):
                logger.error(f"❌ Dry run failed: {result.get('error', 'Unknown error')}")
                sys.exit(1)
        else:
            logger.info(f"🚀 EXECUTION MODE - Applying policies to all tables (force: {args.force})")
            result = apply_policies_to_all_tables(connection, force=args.force)
            
            if result.get('success') is False:
                logger.error(f"❌ Policy application failed: {result.get('error', 'Unknown error')}")
                sys.exit(1)
            
            if result['failed'] > 0:
                logger.warning(f"⚠️ Policy application completed with {result['failed']} failures")
                if result['successful'] == 0:
                    sys.exit(1)
            else:
                logger.info("✅ Policy application completed successfully!")
            
            # Apply security_invoker to all views
            view_result = apply_security_invoker_to_all_views(connection)
            if view_result.get('success') is False:
                logger.error(f"❌ View security update failed: {view_result.get('error', 'Unknown error')}")
                sys.exit(1)
            logger.info(
                f"✅ View security update completed. Updated {view_result.get('updated', 0)} of {view_result.get('total_views', 0)} views"
            )
        
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        sys.exit(1)
    finally:
        if connection:
            connection.close()
            logger.debug("Database connection closed")

if __name__ == "__main__":
    main()
