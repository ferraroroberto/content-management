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
from reporting.process.supabase_uploader import execute_sql, get_db_connection, run_sql_file

SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_notion_relations_system.sql")

# Set up logger. Module-scope default is a real (unconfigured) Logger — never
# None — so `logger.xxx(...)` calls elsewhere in this module are plain Logger
# calls with no Optional to work around. `configure_logger()` replaces it
# with the fully-configured (handlers + formatter) instance.
logger = logging.getLogger("setup_notion_relations_system")

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("setup_notion_relations_system", file_logging=False, level=log_level)
    return logger

def fetch_debug_log_entries(connection, limit=100):
    """Fetch recent debug log entries from the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT msg, created_at FROM view_debug_log(%s);", (limit,))
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"❌ Error fetching debug log: {e}")
        return None

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Set up Notion relations system and initialize it.')
    
    # Add arguments for all interactive prompts
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    return parser.parse_args()

def main(args=None):
    """Main function to set up the Notion relations system."""
    if args is None:
        # Use command-line arguments if available, otherwise parse them
        args = parse_arguments()
    
    # Configure logger with appropriate level based on args
    debug_mode = args.debug
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Notion Relations System Setup")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load configuration
    config = load_config()

    # Connect to database
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Failed to connect to database")
        return

    # Read + execute the setup SQL (creates functions, tables, etc.)
    logger.info("🔄 Executing setup SQL for Notion relations system")
    setup_success = run_sql_file(connection, SQL_PATH, logger=logger)

    if not setup_success:
        connection.close()
        logger.error("❌ Setup SQL execution failed")
        return

    # Run the final initialization query to activate the script
    init_query = "SELECT setup_notion_relations_system();"
    logger.info("⚙️  Running initialization query to activate the system")
    init_success = execute_sql(connection, init_query, logger=logger)
    
    # Fetch recent debug log entries if initialization succeeded
    if init_success:
        logger.info("🧾 Latest setup log messages (up to 100):")
        log_rows = fetch_debug_log_entries(connection, limit=100)
        if log_rows:
            for msg, created_at in log_rows:
                logger.info(f"{created_at} - {msg}")
        else:
            logger.info("(No debug log entries returned)")
    
    # Close connection
    connection.close()
    
    if setup_success and init_success:
        logger.info("✅ Notion Relations System setup and initialization completed successfully")
    else:
        logger.error("❌ Notion Relations System setup completed with errors")

if __name__ == "__main__":
    main()


