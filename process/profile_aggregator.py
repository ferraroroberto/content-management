import os
import sys
import logging
import argparse
import pandas as pd
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger
from process.supabase_uploader import get_db_connection

# Set up logger
logger = None

def configure_logger(debug_mode=False, existing_logger=None):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    if existing_logger:
        logger = existing_logger
    else:
        log_level = logging.DEBUG if debug_mode else logging.INFO
        logger = setup_logger("profile_aggregator", file_logging=False, level=log_level)
    
    return logger

def read_sql_from_file():
    """Read the SQL content from the SQL file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, "profile_aggregator.sql")
    
    try:
        with open(file_path, 'r') as f:
            sql_content = f.read()
        return sql_content
    except Exception as e:
        logger.error(f"❌ Error reading SQL file: {e}")
        return None

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
        
        # Read SQL from file
        logger.debug("📝 Reading SQL from file")
        sql = read_sql_from_file()
        
        if not sql:
            logger.error("❌ Failed to read SQL file")
            return False
        
        # Execute the SQL query
        logger.info("🔄 Executing SQL to create aggregated profile table")
        with connection.cursor() as cursor:
            cursor.execute(sql)
            logger.debug("✓ SQL execution completed")
        
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

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Aggregate profile data from multiple platform tables.')
    
    # Add arguments for all interactive prompts
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
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
