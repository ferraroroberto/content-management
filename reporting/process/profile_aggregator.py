import os
import sys
import logging
import argparse
import pandas as pd
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent.parent))
from config.logger_config import setup_logger
from reporting.process.supabase_uploader import get_db_connection, run_sql_file

SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile_aggregator.sql")

# Set up logger. Module-scope default is a real (unconfigured) Logger — never
# None — so `logger.xxx(...)` calls elsewhere in this module are plain Logger
# calls with no Optional to work around. `configure_logger()` replaces it
# with the fully-configured (handlers + formatter) instance.
logger = logging.getLogger("profile_aggregator")

def configure_logger(debug_mode=False, existing_logger=None):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    if existing_logger:
        logger = existing_logger
    else:
        log_level = logging.DEBUG if debug_mode else logging.INFO
        logger = setup_logger("profile_aggregator", file_logging=False, level=log_level)

    return logger

def aggregate_profile_data(connection=None):
    """
    Aggregate data from all profile tables into a single profile table.
    
    Args:
        connection: Optional database connection
        
    Returns:
        bool: True if successful, False otherwise
    """
    connection_created = False
    if connection is None:
        logger.debug("🔌 No connection provided, creating a new database connection")
        connection = get_db_connection()
        connection_created = True
    
    if connection is None:
        logger.error("❌ Cannot aggregate profile data: No database connection")
        return False
    
    try:
        logger.info("🔄 Starting profile data aggregation")

        # Read + execute the aggregation SQL
        logger.info("🔄 Executing SQL to create aggregated profile table")
        if not run_sql_file(connection, SQL_PATH, logger=logger):
            return False

        # Count rows in the new table
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM profile")
            count = cursor.fetchone()[0]
            logger.debug(f"📊 New table contains {count} rows of data")
            
        logger.info(f"✅ Successfully created aggregated profile table with {count} rows")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error aggregating profile data: {e}")
        logger.debug(f"🔬 Exception details: {type(e).__name__}")
        return False
    finally:
        # Close the connection if we created it
        if connection_created and connection:
            connection.close()
            logger.debug("🔌 Database connection closed")

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Aggregate profile data from multiple platform tables.')

    # Add arguments for all interactive prompts
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--date', type=str, help='Reference date (unused in this module)')

    return parser.parse_args()

def main(args=None):
    """Main function for running the profile aggregator."""
    if args is None:
        # Use command-line arguments if available, otherwise parse them
        args = parse_arguments()
    
    # Configure logger with appropriate level based on args
    debug_mode = args.debug
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Profile Aggregator")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Aggregate profile data
    result = aggregate_profile_data()
    
    if result:
        logger.info("✅ Profile aggregation completed successfully")
    else:
        logger.error("❌ Profile aggregation failed")

if __name__ == "__main__":
    main()
