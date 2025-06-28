import psycopg2
from dotenv import load_dotenv
import os
import sys
from pathlib import Path

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger
logger = setup_logger("supabase_drop_all_tables")

# Load environment variables from .env
load_dotenv()

# Fetch variables
USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

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

    connection.autocommit = True
    cursor = connection.cursor()

    # Fetch all table names in the public schema
    cursor.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public';
    """)
    tables = cursor.fetchall()

    if not tables:
        logger.info("No tables found in the public schema.")
    else:
        for (table_name,) in tables:
            drop_query = f'DROP TABLE IF EXISTS public."{table_name}" CASCADE;'
            cursor.execute(drop_query)
            logger.info(f"Dropped table: {table_name}")

    cursor.close()
    connection.close()
    logger.info("Connection closed.")

except Exception as e:
    logger.error(f"Failed to drop tables: {e}")
