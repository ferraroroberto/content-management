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
from reporting.process.supabase_uploader import get_db_connection, run_sql_file

SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posts_consolidator.sql")

# Set up logger. Module-scope default is a real (unconfigured) Logger — never
# None — so `logger.xxx(...)` calls elsewhere in this module are plain Logger
# calls with no Optional to work around. `configure_logger()` replaces it
# with the fully-configured (handlers + formatter) instance.
logger = logging.getLogger("posts_consolidator")

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("posts_consolidator", file_logging=False, level=log_level)
    return logger

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

    # Connect to database using the approach from profile_aggregator
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Failed to connect to database")
        return

    # Read + execute the consolidation SQL
    logger.info("🔄 Executing SQL to create consolidated posts table")
    success = run_sql_file(connection, SQL_PATH, logger=logger)

    # Close connection
    connection.close()
    
    if success:
        logger.info("✅ Posts Consolidator completed successfully")
    else:
        logger.error("❌ Posts Consolidator failed")

if __name__ == "__main__":
    main()
