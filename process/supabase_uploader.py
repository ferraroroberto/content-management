import json
import os
import pandas as pd
import logging
from pathlib import Path
import sys
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger
logger = None

def configure_logger(existing_logger=None):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    if existing_logger:
        logger = existing_logger
    else:
        logger = setup_logger("supabase_uploader", file_logging=False)
    return logger

def load_db_config():
    """Load database configuration from environment variables."""
    logger.debug("📂 Loading database configuration")
    
    # Load environment variables from .env file
    load_dotenv()
    
    db_config = {
        "user": os.getenv("user"),
        "password": os.getenv("password"),
        "host": os.getenv("host"),
        "port": os.getenv("port"),
        "dbname": os.getenv("dbname")
    }
    
    # Check if all required configuration exists
    missing_keys = [k for k, v in db_config.items() if not v]
    if missing_keys:
        logger.error(f"❌ Database configuration missing: {', '.join(missing_keys)}")
        return None
            
    logger.info("✅ Database configuration loaded successfully")
    return db_config

def get_db_connection(db_config=None):
    """Get a PostgreSQL database connection."""
    if db_config is None:
        db_config = load_db_config()
        
    if not db_config:
        return None
        
    try:
        connection = psycopg2.connect(
            user=db_config.get("user"),
            password=db_config.get("password"),
            host=db_config.get("host"),
            port=db_config.get("port"),
            dbname=db_config.get("dbname")
        )
        connection.autocommit = True
        logger.info("✅ Connected to database successfully")
        return connection
    except Exception as e:
        logger.error(f"❌ Error connecting to database: {e}")
        return None

def map_pandas_to_postgres_type(dtype):
    """Map pandas data types to PostgreSQL data types for table creation."""
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    elif pd.api.types.is_float_dtype(dtype):
        return "double precision"
    elif pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    elif pd.api.types.is_datetime64_dtype(dtype):
        return "timestamp"
    else:
        # Default to text for strings and other types
        return "text"

def table_exists(connection, table_name):
    """Check if a table exists in the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                );
            """, (table_name,))
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"❌ Error checking if table {table_name} exists: {e}")
        return False

def get_table_creation_sql(table_name, df, primary_keys):
    """Generate SQL statement for table creation."""
    column_defs = []
    for col_name, dtype in df.dtypes.items():
        pg_type = map_pandas_to_postgres_type(dtype)
        column_defs.append(f'"{col_name}" {pg_type}')
    
    # Add primary key constraint
    pk_constraint = ""
    if primary_keys:
        quoted_keys = [f'"{key}"' for key in primary_keys]
        pk_constraint = f', PRIMARY KEY ({", ".join(quoted_keys)})'
    
    # Build CREATE TABLE SQL statement
    create_table_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(column_defs)}{pk_constraint});'
    return create_table_sql

def create_table(connection, table_name, df, primary_keys):
    """Create a new table in the database based on DataFrame structure."""
    try:
        # Generate CREATE TABLE SQL
        create_table_sql = get_table_creation_sql(table_name, df, primary_keys)
        logger.info(f"Creating table with SQL: {create_table_sql}")
        
        # Execute the SQL
        with connection.cursor() as cursor:
            cursor.execute(create_table_sql)
        
        logger.info(f"✅ Successfully created table {table_name}")
        return True
    except Exception as e:
        logger.error(f"❌ Error creating table {table_name}: {e}")
        return False

def upload_dataframe_to_db(df, table_name, primary_keys, connection=None):
    """Upload DataFrame to database, creating table if it doesn't exist."""
    connection_created = False
    if connection is None:
        connection = get_db_connection()
        connection_created = True
    
    if connection is None:
        logger.error("❌ Cannot upload data: No database connection")
        return False
    
    try:
        # Check if all primary key columns exist in the DataFrame
        missing_pk_cols = [col for col in primary_keys if col not in df.columns]
        if missing_pk_cols:
            logger.error(f"❌ Primary key columns {missing_pk_cols} not found in DataFrame")
            return False
        
        # Check if table exists
        table_already_exists = table_exists(connection, table_name)
        if not table_already_exists:
            logger.info(f"🔍 Table {table_name} does not exist. Creating...")
            if not create_table(connection, table_name, df, primary_keys):
                logger.error(f"❌ Failed to create table {table_name}. Cannot upload data.")
                return False
        
        num_inserted = len(df)
        num_updated = 0
        
        if table_already_exists:
            with connection.cursor() as cursor:
                pk_columns_str_db = ', '.join([f'"{pk}"' for pk in primary_keys])
                select_pks_sql = f'SELECT {pk_columns_str_db} FROM "{table_name}"'
                cursor.execute(select_pks_sql)
                existing_pks = {tuple(map(str, row)) for row in cursor.fetchall()}

            df_pks = df[primary_keys].astype(str)
            df_pk_tuples_list = [tuple(row) for row in df_pks.to_numpy()]
            
            all_pks = set(existing_pks)
            inserted_count = 0
            updated_count = 0
            for pk_tuple in df_pk_tuples_list:
                if pk_tuple in all_pks:
                    updated_count += 1
                else:
                    inserted_count += 1
                    all_pks.add(pk_tuple)
            
            num_inserted = inserted_count
            num_updated = updated_count
        
        # Clean the DataFrame to ensure compatibility with PostgreSQL
        df = df.copy()
        
        # Convert booleans to integers (0/1)
        for col in df.select_dtypes(include=['bool']).columns:
            df[col] = df[col].astype(int)
        
        # Replace NaN values with None for SQL compatibility
        df = df.where(pd.notnull(df), None)
        
        # Convert DataFrame to list of tuples for batch insertion
        records = [tuple(row) for row in df.values]
        columns = df.columns.tolist()
        
        if not records:
            logger.warning(f"⚠️ No records to upload to {table_name}")
            return True  # Not an error, just nothing to do
        
        # For upsert, we need to specify the primary key columns
        logger.info(f"📤 Uploading {len(records)} records to {table_name}")
        
        # Prepare SQL for upsert operation
        columns_str = ', '.join([f'"{col}"' for col in columns])
        placeholders = ', '.join(['%s'] * len(columns))
        
        # Construct the ON CONFLICT clause for upsert
        pk_columns_str = ', '.join([f'"{pk}"' for pk in primary_keys])
        update_set = ', '.join([f'"{col}" = EXCLUDED."{col}"' for col in columns if col not in primary_keys])
        
        upsert_sql = f"""
            INSERT INTO "{table_name}" ({columns_str})
            VALUES ({placeholders})
            ON CONFLICT ({pk_columns_str})
            DO UPDATE SET {update_set}
        """
        
        # Upload in batches to avoid memory issues
        batch_size = 1000
        success = True
        
        with connection.cursor() as cursor:
            for i in range(0, len(records), batch_size):
                batch = records[i:i+batch_size]
                logger.debug(f"Uploading batch {i//batch_size + 1}/{(len(records)-1)//batch_size + 1} ({len(batch)} records)")
                
                try:
                    # Use execute_batch for efficient batch insertion
                    psycopg2.extras.execute_batch(cursor, upsert_sql, batch)
                except Exception as e:
                    logger.error(f"❌ Error uploading batch to {table_name}: {e}")
                    success = False
        
        if success:
            if table_already_exists:
                logger.info(f"✅ Data upload to {table_name} complete. New records: {num_inserted}. Records skipped (PK existed): {num_updated}.")
            else:
                logger.info(f"✅ Successfully uploaded all data to {table_name}")
        return success
    
    except Exception as e:
        logger.error(f"❌ Error uploading to {table_name}: {e}")
        return False
    finally:
        # Close the connection if we created it
        if connection_created and connection:
            connection.close()
            logger.debug("Database connection closed")

def get_primary_keys(platform, data_type):
    """
    Determine the primary key columns based on platform and data type.
    
    Args:
        platform (str): The platform name (e.g., 'facebook', 'instagram', etc.)
        data_type (str): The type of data ('posts', 'profile', etc.)
        
    Returns:
        list: List of column names that should be used as primary keys
    """
    data_type = data_type.lower()
    
    # Default primary keys for common data types
    if data_type == 'posts':
        return ['date', 'platform', 'data_type', 'post_id']
    elif data_type == 'profile':
        return ['date', 'platform', 'data_type']
    elif data_type == 'comments':
        return ['comment_id']
    elif data_type == 'insights' or data_type == 'metrics':
        # For insights/metrics, typically need combination of id and date
        return ['post_id', 'date']
    elif data_type == 'audience':
        # For audience data, might be segmented by date
        return ['date']
    
    # Platform-specific adjustments if needed
    if platform.lower() == 'facebook':
        if data_type == 'ads':
            return ['ad_id']
    elif platform.lower() == 'instagram':
        if data_type == 'stories':
            return ['story_id']
    elif platform.lower() == 'linkedin':
        if data_type == 'followers':
            return ['date']
    
    # If no specific rule, use a generic id column based on data_type
    return [f'{data_type.rstrip("s")}_id']

def upload_all_dataframes(dataframes, db_config=None):
    """Upload all DataFrames to the database."""
    if not dataframes:
        logger.warning("⚠️ No DataFrames to upload")
        return False
    
    # Get database connection
    connection = get_db_connection(db_config)
    if not connection:
        return False
    
    try:
        success = True
        for key, df in dataframes.items():
            # Parse platform and data_type from key (format: "platform_datatype")
            parts = key.split('_', 1)
            if len(parts) != 2:
                logger.warning(f"⚠️ Invalid key format: {key}, skipping")
                continue
            
            platform, data_type = parts
            
            # Determine primary keys based on data type
            primary_keys = get_primary_keys(platform, data_type)
            
            # Check if post_id is in the DataFrame for posts tables
            if data_type.lower() == 'posts' and 'post_id' not in df.columns:
                logger.warning(f"⚠️ Required primary key 'post_id' not found in DataFrame for {key}")
                # Look for alternative id columns that could be renamed
                potential_id_cols = [col for col in df.columns if 'id' in col.lower()]
                if potential_id_cols:
                    # Use the first id column found
                    logger.info(f"🔄 Using '{potential_id_cols[0]}' as 'post_id'")
                    df['post_id'] = df[potential_id_cols[0]]
                else:
                    logger.error(f"❌ No suitable ID column found for {key}, skipping")
                    success = False
                    continue
            
            # Upload DataFrame to database
            if not upload_dataframe_to_db(df, key, primary_keys, connection):
                success = False
        
        return success
    finally:
        # Close the connection
        if connection:
            connection.close()
            logger.debug("Database connection closed")

def main():
    """Main function for testing the module independently."""
    # Configure logger
    configure_logger()
    
    logger.info("🚀 Starting Database Uploader Test")
    
    # Load test DataFrame
    try:
        # Example: load a CSV file
        csv_path = input("Enter path to CSV file for testing: ")
        df = pd.read_csv(csv_path)
        
        # Get table name from file name
        table_name = os.path.splitext(os.path.basename(csv_path))[0]
        
        # Determine if this is a posts or profile table
        is_posts = 'posts' in table_name.lower()
        primary_keys = get_primary_keys('test', 'posts' if is_posts else 'profile')
        
        # Upload to database
        upload_dataframe_to_db(df, table_name, primary_keys)
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
    
if __name__ == "__main__":
    main()