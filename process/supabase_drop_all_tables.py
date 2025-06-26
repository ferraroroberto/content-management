import psycopg2
from dotenv import load_dotenv
import os

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
    print("Connection successful!")

    connection.autocommit = True
    cursor = connection.cursor()

    # Fetch all table names in the public schema
    cursor.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public';
    """)
    tables = cursor.fetchall()

    if not tables:
        print("No tables found in the public schema.")
    else:
        for (table_name,) in tables:
            drop_query = f'DROP TABLE IF EXISTS public."{table_name}" CASCADE;'
            cursor.execute(drop_query)
            print(f"Dropped table: {table_name}")

    cursor.close()
    connection.close()
    print("Connection closed.")

except Exception as e:
    print(f"Failed to drop tables: {e}")
