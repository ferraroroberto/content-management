import psycopg2
from dotenv import load_dotenv
import os
import sys
from pathlib import Path

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger
logger = setup_logger("supabase_test_create_table")

# Load environment variables from .env
load_dotenv()

# Fetch variables
USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

# SQL statement to create the table
create_table_query = """
CREATE TABLE IF NOT EXISTS test (
    date DATE NOT NULL,
    platform VARCHAR NOT NULL,
    data_type VARCHAR NOT NULL,
    post_id VARCHAR NOT NULL,
    posted_at DATE,
    is_video INTEGER,
    num_likes INTEGER,
    num_comments INTEGER,
    num_reshares INTEGER,
    PRIMARY KEY (date, platform, data_type, post_id)
);
"""

# Connect to the database
try:
    connection = psycopg2.connect(
        user=USER,
        password=PASSWORD,
        host=HOST,
        port=PORT,
        dbname=DBNAME
    )
    logger.info("Connection successful!")
    
    # Set autocommit to avoid transaction issues
    connection.autocommit = True
    
    # Create a cursor to execute SQL queries
    cursor = connection.cursor()
    
    # Execute the table creation query
    cursor.execute(create_table_query)
    logger.info("Table 'test' created successfully or already exists.")

    # Close the cursor and connection
    cursor.close()
    connection.close()
    logger.info("Connection closed.")

except Exception as e:
    logger.error(f"Failed to create table: {e}")
