import os
import json
import logging
import sys
import argparse
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent.parent))
from config.logger_config import setup_logger
from config.loader import load_full_config as load_config
from reporting.process.supabase_uploader import get_db_connection

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("posts_consolidator", file_logging=False, level=log_level)
    return logger

def read_sql_from_file():
    """Read the SQL content from the existing SQL file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, "posts_consolidator.sql")
    
    try:
        with open(file_path, 'r') as f:
            sql_content = f.read()
        return sql_content
    except Exception as e:
        logger.error(f"❌ Error reading SQL file: {e}")
        return None

def execute_sql(connection, sql_content):
    """Execute the SQL on the Supabase database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql_content)
        connection.commit()
        logger.info("✅ SQL executed successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error executing SQL: {e}")
        connection.rollback()
        return False

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Execute SQL to consolidate posts data.')
    
    # Add arguments for all interactive prompts
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--date', type=str, help='Reference date (unused in this module)')
    
    return parser.parse_args()

def main(args=None):
    """Main function to execute the posts consolidator."""
    if args is None:
        # Use command-line arguments if available, otherwise parse them
        args = parse_arguments()
    
    # Configure logger with appropriate level based on args
    debug_mode = args.debug
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Posts Consolidator")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load configuration
    config = load_config()

    # Read SQL from file
    logger.info("📝 Reading SQL from file")
    sql_content = read_sql_from_file()
    
    if not sql_content:
        logger.error("❌ Failed to read SQL file")
        return
    
    # Connect to database using the approach from profile_aggregator
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Failed to connect to database")
        return
    
    # Execute SQL
    logger.info("🔄 Executing SQL to create consolidated posts table")
    success = execute_sql(connection, sql_content)
    
    # Close connection
    connection.close()
    
    if success:
        logger.info("✅ Posts Consolidator completed successfully")
    else:
        logger.error("❌ Posts Consolidator failed")

if __name__ == "__main__":
    main()
