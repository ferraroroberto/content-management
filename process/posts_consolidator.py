import os
import json
import logging
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger
from process.supabase_uploader import get_db_connection, load_db_config, configure_logger as configure_supabase_logger

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("posts_consolidator", file_logging=False, level=log_level)
    
    # Also configure the logger for supabase_uploader
    configure_supabase_logger(logger)
    
    return logger

def load_config():
    """Load main configuration from config.json file."""
    logger.debug("📂 Loading main configuration file")
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
    config_path = os.path.join(config_dir, 'config.json')
    
    try:
        with open(config_path, 'r') as file:
            logger.info("✅ Main configuration loaded successfully")
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"❌ Error: Main configuration file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        logger.error(f"❌ Error: Invalid JSON in main configuration file at {config_path}")
        return None

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

def main():
    """Main function to execute the posts consolidator."""
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode? (y/n): ").lower()
    debug_mode = debug_input == 'y'
    
    # Configure logger with appropriate level
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Posts Consolidator")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load configuration
    config = load_config()
    if not config:
        logger.error("❌ Failed to load configuration")
        return
    
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
        logger.error("❌ Posts Consolidator failed")

if __name__ == "__main__":
    main()
