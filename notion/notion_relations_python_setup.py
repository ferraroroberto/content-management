#!/usr/bin/env python3
"""
notion_relations_python_setup.py

Python wrapper for the new simplified Notion relations system.
Integrates with existing project structure while using the new SQL core.
Follows project automation standards with logging and configuration management.
"""

import json
import os
import sys
import logging
from pathlib import Path
import psycopg2
import psycopg2.extras
import argparse
from dotenv import load_dotenv
from typing import Dict, List, Any, Optional, Tuple, Set

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger
from process.supabase_uploader import get_db_connection, load_db_config

# Set up logger - will use existing logger if available
logger = logging.getLogger("notion_relations_python_setup")
if not logger.handlers:
    logger = setup_logger("notion_relations_python_setup", file_logging=False)

class NotionRelationsPythonSetup:
    """Python wrapper for the new simplified Notion relations system."""
    
    def __init__(self, config_path: str = None, environment: str = "cloud"):
        """Initialize the setup with configuration."""
        self.environment = environment
        self.config = self._load_config(config_path)
        self.connection = None
        
    def _load_config(self, config_path: str = None) -> dict:
        """Load configuration from JSON file."""
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "config.json"
        
        logger.debug(f"📂 Loading configuration from {config_path}")
        
        if not os.path.exists(config_path):
            logger.error(f"❌ Configuration file not found: {config_path}")
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        logger.info("✅ Configuration loaded successfully")
        return config
    
    def _get_database_connection(self):
        """Get database connection using existing project infrastructure."""
        try:
            db_config = load_db_config(self.environment)
            connection = get_db_connection(db_config, self.environment)
            
            if not connection:
                logger.error("❌ Failed to connect to database")
                return None
            
            logger.info("✅ Database connection established")
            return connection
            
        except Exception as e:
            logger.error(f"❌ Error connecting to database: {e}")
            return None
    
    def _read_sql_file(self, file_path: str) -> str:
        """Read SQL file content."""
        try:
            sql_file = Path(__file__).parent / file_path
            if not sql_file.exists():
                logger.error(f"❌ SQL file not found: {sql_file}")
                return None
            
            with open(sql_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            logger.debug(f"✅ Read SQL file: {file_path}")
            return content
            
        except Exception as e:
            logger.error(f"❌ Error reading SQL file {file_path}: {e}")
            return None
    
    def _execute_sql_script(self, connection, sql_content: str, script_name: str) -> bool:
        """Execute SQL script content."""
        if not sql_content:
            logger.error(f"❌ No SQL content to execute for {script_name}")
            return False
        
        try:
            with connection.cursor() as cursor:
                # Split SQL by semicolons and execute each statement
                statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
                
                for i, statement in enumerate(statements):
                    if statement and not statement.startswith('--'):
                        try:
                            cursor.execute(statement)
                            logger.debug(f"✅ Executed statement {i+1}/{len(statements)} in {script_name}")
                        except Exception as e:
                            # Skip statements that might fail (like DROP IF EXISTS)
                            if "does not exist" not in str(e).lower():
                                logger.warning(f"⚠️ Statement {i+1} in {script_name} failed: {e}")
                
                connection.commit()
                logger.info(f"✅ Successfully executed SQL script: {script_name}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error executing SQL script {script_name}: {e}")
            connection.rollback()
            return False
    
    def setup_core_system(self) -> bool:
        """Set up the core simplified relations system."""
        logger.info("🚀 Setting up core simplified Notion relations system...")
        
        if not self.connection:
            logger.error("❌ No database connection available")
            return False
        
        try:
            # Step 1: Load and execute core functions
            logger.info("📦 Step 1: Loading core functions...")
            core_sql = self._read_sql_file("auto_relations_detector.sql")
            if not core_sql:
                return False
            
            if not self._execute_sql_script(self.connection, core_sql, "auto_relations_detector.sql"):
                return False
            
            logger.info("✅ Core functions loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error setting up core system: {e}")
            return False
    
    def setup_automatic_relations(self) -> bool:
        """Set up automatic relation detection and handling."""
        logger.info("🔍 Setting up automatic relation detection...")
        
        if not self.connection:
            logger.error("❌ No database connection available")
            return False
        
        try:
            # Step 2: Create dynamic relation columns
            logger.info("📊 Step 2: Creating dynamic relation columns...")
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT create_dynamic_relation_columns();")
                result = cursor.fetchone()
                logger.info("✅ Dynamic relation columns created")
            
            # Step 3: Create universal relation views
            logger.info("👁️ Step 3: Creating universal relation views...")
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT create_universal_relation_views();")
                result = cursor.fetchone()
                logger.info("✅ Universal relation views created")
            
            # Step 4: Create automatic indexes
            logger.info("⚡ Step 4: Creating automatic indexes...")
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT create_automatic_relation_indexes();")
                result = cursor.fetchone()
                logger.info("✅ Automatic indexes created")
            
            self.connection.commit()
            return True
            
        except Exception as e:
            logger.error(f"❌ Error setting up automatic relations: {e}")
            self.connection.rollback()
            return False
    
    def verify_setup(self) -> bool:
        """Verify that the setup was successful."""
        logger.info("🔍 Verifying setup...")
        
        if not self.connection:
            logger.error("❌ No database connection available")
            return False
        
        try:
            verification_results = {}
            
            # Check 1: Core functions exist
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT routine_name 
                    FROM information_schema.routines 
                    WHERE routine_name IN (
                        'auto_detect_all_notion_relations',
                        'create_dynamic_relation_columns',
                        'create_universal_relation_views',
                        'smart_resolve_relations',
                        'create_automatic_relation_indexes'
                    )
                    AND routine_schema = 'public'
                """)
                functions = [row[0] for row in cursor.fetchall()]
                verification_results['core_functions'] = len(functions) == 5
                logger.info(f"✅ Core functions: {len(functions)}/5 found")
            
            # Check 2: Computed columns created
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM information_schema.columns 
                    WHERE table_name LIKE 'notion_%' 
                    AND column_name LIKE 'rel_%'
                    AND is_generated = 'ALWAYS'
                """)
                computed_columns = cursor.fetchone()[0]
                verification_results['computed_columns'] = computed_columns > 0
                logger.info(f"✅ Computed columns: {computed_columns} found")
            
            # Check 3: Universal views created
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM information_schema.tables 
                    WHERE table_name LIKE '%_universal_relations'
                    AND table_schema = 'public'
                """)
                universal_views = cursor.fetchone()[0]
                verification_results['universal_views'] = universal_views > 0
                logger.info(f"✅ Universal views: {universal_views} found")
            
            # Check 4: GIN indexes created
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM pg_indexes 
                    WHERE indexname LIKE 'idx_notion_%_%'
                    AND indexdef LIKE '%GIN%'
                """)
                gin_indexes = cursor.fetchone()[0]
                verification_results['gin_indexes'] = gin_indexes > 0
                logger.info(f"✅ GIN indexes: {gin_indexes} found")
            
            # Overall verification
            all_checks_passed = all(verification_results.values())
            
            if all_checks_passed:
                logger.info("🎉 All verification checks passed! Setup successful.")
            else:
                failed_checks = [k for k, v in verification_results.items() if not v]
                logger.warning(f"⚠️ Some verification checks failed: {failed_checks}")
            
            return all_checks_passed
            
        except Exception as e:
            logger.error(f"❌ Error during verification: {e}")
            return False
    
    def run_demo_queries(self) -> bool:
        """Run demo queries to show the system working."""
        logger.info("🎯 Running demo queries...")
        
        if not self.connection:
            logger.error("❌ No database connection available")
            return False
        
        try:
            # Demo 1: Check detected relations
            logger.info("🔍 Demo 1: Checking detected relations...")
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT * FROM auto_detect_all_notion_relations() LIMIT 5;")
                relations = cursor.fetchall()
                logger.info(f"✅ Detected {len(relations)} relation patterns")
                
                for relation in relations:
                    logger.info(f"   📊 {relation[0]}.{relation[1]} -> {relation[2]}")
            
            # Demo 2: Check universal views
            logger.info("👁️ Demo 2: Checking universal views...")
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_name LIKE '%_universal_relations'
                    ORDER BY table_name
                """)
                views = cursor.fetchall()
                logger.info(f"✅ Found {len(views)} universal relation views:")
                
                for view in views:
                    logger.info(f"   📋 {view[0]}")
            
            # Demo 3: Sample data from universal views
            logger.info("📊 Demo 3: Sampling data from universal views...")
            for view in views:
                view_name = view[0]
                try:
                    with self.connection.cursor() as cursor:
                        cursor.execute(f"SELECT COUNT(*) FROM {view_name};")
                        count = cursor.fetchone()[0]
                        logger.info(f"   📈 {view_name}: {count} rows")
                except Exception as e:
                    logger.warning(f"   ⚠️ Could not query {view_name}: {e}")
            
            logger.info("✅ Demo queries completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error running demo queries: {e}")
            return False
    
    def setup_complete_system(self) -> bool:
        """Set up the complete system in the correct order."""
        logger.info("🚀 Starting complete setup of simplified Notion relations system...")
        
        try:
            # Get database connection
            self.connection = self._get_database_connection()
            if not self.connection:
                return False
            
            # Step 1: Setup core system
            if not self.setup_core_system():
                logger.error("❌ Core system setup failed")
                return False
            
            # Step 2: Setup automatic relations
            if not self.setup_automatic_relations():
                logger.error("❌ Automatic relations setup failed")
                return False
            
            # Step 3: Verify setup
            if not self.verify_setup():
                logger.error("❌ Setup verification failed")
                return False
            
            # Step 4: Run demo queries
            if not self.run_demo_queries():
                logger.warning("⚠️ Demo queries failed, but setup may still be successful")
            
            logger.info("🎉 Complete setup finished successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error during complete setup: {e}")
            return False
        
        finally:
            if self.connection:
                self.connection.close()
                logger.info("🔌 Database connection closed")
    
    def run_quick_test(self) -> bool:
        """Run a quick test to verify the system is working."""
        logger.info("🧪 Running quick test...")
        
        try:
            # Get database connection
            self.connection = self._get_database_connection()
            if not self.connection:
                return False
            
            # Test 1: Check if functions exist
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT routine_name 
                    FROM information_schema.routines 
                    WHERE routine_name = 'auto_detect_all_notion_relations'
                    AND routine_schema = 'public'
                """)
                if cursor.fetchone():
                    logger.info("✅ Core functions are available")
                else:
                    logger.error("❌ Core functions not found - run setup first")
                    return False
            
            # Test 2: Try to detect relations
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT * FROM auto_detect_all_notion_relations() LIMIT 3;")
                relations = cursor.fetchall()
                logger.info(f"✅ System detected {len(relations)} relation patterns")
                
                for relation in relations[:3]:
                    logger.info(f"   📊 {relation[0]}.{relation[1]} -> {relation[2]}")
            
            logger.info("✅ Quick test passed!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Quick test failed: {e}")
            return False
        
        finally:
            if self.connection:
                self.connection.close()

def main():
    """Main entry point for the Python setup script."""
    parser = argparse.ArgumentParser(
        description="Python wrapper for simplified Notion relations system"
    )
    parser.add_argument(
        "--config", 
        type=str, 
        help="Path to configuration file"
    )
    parser.add_argument(
        "--environment", 
        type=str, 
        default="cloud", 
        choices=["cloud", "local"],
        help="Database environment (default: cloud)"
    )
    parser.add_argument(
        "--action", 
        type=str, 
        default="setup",
        choices=["setup", "test", "verify", "demo"],
        help="Action to perform (default: setup)"
    )
    
    args = parser.parse_args()
    
    try:
        # Initialize the setup system
        setup_system = NotionRelationsPythonSetup(
            config_path=args.config,
            environment=args.environment
        )
        
        # Perform the requested action
        if args.action == "setup":
            success = setup_system.setup_complete_system()
        elif args.action == "test":
            success = setup_system.run_quick_test()
        elif args.action == "verify":
            setup_system.connection = setup_system._get_database_connection()
            success = setup_system.verify_setup()
        elif args.action == "demo":
            setup_system.connection = setup_system._get_database_connection()
            success = setup_system.run_demo_queries()
        else:
            logger.error(f"❌ Unknown action: {args.action}")
            return 1
        
        if success:
            logger.info("✅ Action completed successfully")
            return 0
        else:
            logger.error("❌ Action failed")
            return 1
            
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    exit(main())