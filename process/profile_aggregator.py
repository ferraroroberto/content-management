import os
import sys
import logging
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
        
        # List of all platform tables to join
        platforms = ['linkedin', 'instagram', 'twitter', 'substack', 'threads']
        logger.debug(f"🔍 Processing data for platforms: {', '.join(platforms)}")
        
        # Create the SQL query to join all tables
        logger.debug("🔧 Building SQL query for aggregation")
        
        sql = """
        DROP TABLE IF EXISTS profile;
        
        CREATE TABLE profile AS
        WITH base_dates AS (
            SELECT DISTINCT date 
            FROM (
        """
        
        # Add all the UNION clauses to get all unique dates
        date_queries = []
        for platform in platforms:
            date_queries.append(f"SELECT date FROM {platform}_profile")
        
        sql += " UNION ".join(date_queries)
        
        sql += """
            ) as all_dates
        )
        
        SELECT 
            base_dates.date
        """
        
        # Add columns for each platform with appropriate suffixes
        for platform in platforms:
            sql += f"""
            , {platform}_profile.num_followers as num_followers_{platform}
            """
        
        # Add the FROM clause with all the LEFT JOINs
        sql += """
        FROM base_dates
        """
        
        # Add LEFT JOIN for each platform
        for platform in platforms:
            sql += f"""
            LEFT JOIN {platform}_profile 
            ON base_dates.date = {platform}_profile.date 
            AND {platform}_profile.platform = '{platform}' 
            AND {platform}_profile.data_type = 'profile'
            """
        
        # Add ORDER BY clause
        sql += """
        ORDER BY base_dates.date;
        """
        
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

def main():
    """Main function for running the profile aggregator."""
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode? (y/n): ").lower()
    debug_mode = debug_input == 'y'
    
    # Configure logger with appropriate level
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
