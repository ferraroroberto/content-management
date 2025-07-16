import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Set, Tuple
import pandas as pd
import requests
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger
from process.supabase_uploader import get_db_connection, load_db_config

# Set up logger - will use existing logger if available
logger = logging.getLogger("notion_supabase_sync")
if not logger.handlers:
    logger = setup_logger("notion_supabase_sync", file_logging=False)

class NotionSupabaseSync:
    """Sync Notion databases to Supabase PostgreSQL."""
    
    def __init__(self, config_path: str = None, environment: str = "cloud", database_list_path: str = None):  # type: ignore
        """Initialize the sync with configuration."""
        self.environment = environment
        self.config = self._load_config(config_path)
        self.notion_token = self.config["notion"]["api_token"]
        self.poll_every = self.config["notion"]["poll_every"]
        self.page_size = self.config["notion"]["page_size"]
        
        # Load databases from notion_database_list.json
        self.databases = self._load_database_list(database_list_path)
        
        self.headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.last_sync_times = {}  # Track last sync time per database
        
    def _load_config(self, config_path: str = None) -> dict:  # type: ignore
        """Load configuration from JSON file."""
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "config.json"
        
        logger.debug(f"📂 Loading configuration from {config_path}")
        
        if not os.path.exists(config_path):
            logger.error(f"❌ Configuration file not found: {config_path}")
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        logger.info("✅ Configuration loaded successfully")
        return config
    
    def _load_database_list(self, database_list_path: str = None) -> List[dict]:  # type: ignore
        """Load database list from JSON file and filter by replication status."""
        if database_list_path is None:
            database_list_path = Path(__file__).parent / "notion_database_list.json"
        
        logger.debug(f"📂 Loading database list from {database_list_path}")
        
        if not os.path.exists(database_list_path):
            logger.error(f"❌ Database list file not found: {database_list_path}")
            raise FileNotFoundError(f"Database list file not found: {database_list_path}")
        
        with open(database_list_path, 'r') as f:
            all_databases = json.load(f)
        
        # Filter databases where replication is true
        databases_to_sync = [db for db in all_databases if db.get("replication", False)]
        
        logger.info(f"✅ Found {len(databases_to_sync)} databases to sync (out of {len(all_databases)} total)")
        
        # Log the databases that will be synced
        for db in databases_to_sync:
            logger.debug(f"  📊 Will sync: {db['name']} → {db['supabase_table']}")
        
        return databases_to_sync
    
    def _notion_api_call(self, endpoint: str, method: str = "GET", data: dict = None) -> dict:  # type: ignore
        """Make a Notion API call with rate limiting."""
        url = f"https://api.notion.com/v1/{endpoint}"
        
        # Rate limit: 3 requests per second
        time.sleep(0.35)  # ~2.8 requests per second to be safe
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers)
            elif method == "POST":
                response = requests.post(url, headers=self.headers, json=data)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Notion API error: {e}")
            return None  # type: ignore
    
    def _get_database_schema(self, database_id: str) -> dict:
        """Retrieve the schema (properties) of a Notion database."""
        result = self._notion_api_call(f"databases/{database_id}")
        if result:
            return result.get("properties", {})
        return {}
    
    def _query_database(self, database_id: str, start_cursor: str = None, # type: ignore
                       filter_after: datetime = None) -> dict:  # type: ignore
        """Query a Notion database with optional filtering."""
        data = {
            "page_size": self.page_size
        }
        
        if start_cursor:
            data["start_cursor"] = start_cursor
        
        if filter_after:
            data["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": filter_after.isoformat()
                }
            }
        
        return self._notion_api_call(f"databases/{database_id}/query", method="POST", data=data)
    
    def _extract_property_value(self, prop: dict) -> Any:
        """Extract the actual value from a Notion property."""
        prop_type = prop.get("type")
        
        if prop_type == "title":
            texts = prop.get("title", [])
            return "".join([t.get("plain_text", "") for t in texts])
        elif prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            return "".join([t.get("plain_text", "") for t in texts])
        elif prop_type == "number":
            return prop.get("number")
        elif prop_type == "select":
            select = prop.get("select")
            return select.get("name") if select else None
        elif prop_type == "multi_select":
            return [opt.get("name") for opt in prop.get("multi_select", [])]
        elif prop_type == "date":
            date_obj = prop.get("date")
            if date_obj:
                start_date = date_obj.get("start")
                # Return None for empty dates instead of empty string
                return start_date if start_date else None
            return None
        elif prop_type == "checkbox":
            return prop.get("checkbox", False)
        elif prop_type == "url":
            url = prop.get("url")
            # Return None for empty URLs
            return url if url else None
        elif prop_type == "email":
            email = prop.get("email")
            return email if email else None
        elif prop_type == "phone_number":
            phone = prop.get("phone_number")
            return phone if phone else None
        elif prop_type == "formula":
            formula = prop.get("formula", {})
            return self._extract_formula_value(formula)
        elif prop_type == "relation":
            return [rel.get("id") for rel in prop.get("relation", [])]
        elif prop_type == "rollup":
            rollup = prop.get("rollup", {})
            return self._extract_rollup_value(rollup)
        elif prop_type == "people":
            return [person.get("id") for person in prop.get("people", [])]
        elif prop_type == "files":
            files = prop.get("files", [])
            return [f.get("file", {}).get("url") or f.get("external", {}).get("url") for f in files]
        elif prop_type == "created_time":
            created = prop.get("created_time")
            return created if created else None
        elif prop_type == "created_by":
            return prop.get("created_by", {}).get("id")
        elif prop_type == "last_edited_time":
            edited = prop.get("last_edited_time")
            return edited if edited else None
        elif prop_type == "last_edited_by":
            return prop.get("last_edited_by", {}).get("id")
        elif prop_type == "status":
            status = prop.get("status")
            return status.get("name") if status else None
        else:
            # For unknown types, store as JSONB
            return prop
    
    def _extract_formula_value(self, formula: dict) -> Any:
        """Extract value from formula property."""
        formula_type = formula.get("type")
        if formula_type == "string":
            return formula.get("string")
        elif formula_type == "number":
            return formula.get("number")
        elif formula_type == "boolean":
            return formula.get("boolean")
        elif formula_type == "date":
            date_obj = formula.get("date")
            return date_obj.get("start") if date_obj else None
        return None
    
    def _extract_rollup_value(self, rollup: dict) -> Any:
        """Extract value from rollup property."""
        rollup_type = rollup.get("type")
        if rollup_type == "number":
            return rollup.get("number")
        elif rollup_type == "array":
            return [self._extract_property_value(item) for item in rollup.get("array", [])]
        return None
    
    def _normalize_column_name(self, name: str) -> str:
        """Normalize Notion property names to valid PostgreSQL column names."""
        # Replace spaces and special characters with underscores
        normalized = name.lower().strip()
        normalized = "".join(c if c.isalnum() or c == "_" else "_" for c in normalized)
        # Remove consecutive underscores
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        # Ensure it doesn't start with a number
        if normalized and normalized[0].isdigit():
            normalized = f"col_{normalized}"
        return normalized or "unnamed_column"
    
    def _transform_page_to_row(self, page: dict, schema: dict) -> dict:
        """Transform a Notion page into a flat row for PostgreSQL."""
        row = {
            "notion_id": page["id"],
            "created_time": page["created_time"],
            "last_edited_time": page["last_edited_time"],
            "archived": page.get("archived", False)
        }
        
        # Extract all properties
        properties = page.get("properties", {})
        jsonb_fallback = {}
        
        for prop_name, prop_value in properties.items():
            col_name = self._normalize_column_name(prop_name)
            value = self._extract_property_value(prop_value)
            
            # Handle complex types that don't fit well in regular columns
            if isinstance(value, (list, dict)):
                jsonb_fallback[prop_name] = value
            else:
                # Convert empty strings to None for proper NULL handling
                if value == "":
                    value = None
                row[col_name] = value
        
        # Store complex data in JSONB column
        if jsonb_fallback:
            row["notion_data_jsonb"] = json.dumps(jsonb_fallback)
        
        return row
    
    def _get_postgres_type(self, value: Any) -> str:
        """Determine PostgreSQL type from Python value."""
        if value is None:
            return "text"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "bigint"
        elif isinstance(value, float):
            return "double precision"
        elif isinstance(value, str):
            if len(value) > 255:
                return "text"
            # Check if it's a datetime string
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return "timestamp with time zone"
            except:
                return "text"
        else:
            return "jsonb"
    
    def _create_or_alter_table(self, connection, table_name: str, rows: List[dict]):
        """Create table if not exists, or alter it to add new columns."""
        if not rows:
            return
        
        # Get all unique columns from rows
        all_columns = set()
        column_types = {}
        
        for row in rows:
            for col, val in row.items():
                all_columns.add(col)
                if col not in column_types and val is not None:
                    column_types[col] = self._get_postgres_type(val)
        
        # Set default types for columns we haven't determined
        for col in all_columns:
            if col not in column_types:
                column_types[col] = "text"
        
        # Always include system columns
        column_types["notion_id"] = "text"
        column_types["created_time"] = "timestamp with time zone"
        column_types["last_edited_time"] = "timestamp with time zone"
        column_types["archived"] = "boolean"
        column_types["notion_data_jsonb"] = "jsonb"
        
        with connection.cursor() as cursor:
            # Check if table exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                );
            """, (table_name,))
            table_exists = cursor.fetchone()[0]
            
            if not table_exists:
                # Create table
                columns_sql = []
                for col, dtype in column_types.items():
                    columns_sql.append(f'"{col}" {dtype}')
                
                create_sql = f"""
                    CREATE TABLE "{table_name}" (
                        {', '.join(columns_sql)},
                        PRIMARY KEY (notion_id)
                    );
                """
                cursor.execute(create_sql)
                logger.info(f"✅ Created table {table_name}")
                
                # Create index on last_edited_time for efficient delta queries
                cursor.execute(f"""
                    CREATE INDEX idx_{table_name}_last_edited 
                    ON "{table_name}" (last_edited_time);
                """)
            else:
                # Get existing columns
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = %s;
                """, (table_name,))
                existing_columns = {row[0] for row in cursor.fetchall()}
                
                # Add missing columns
                for col, dtype in column_types.items():
                    if col not in existing_columns:
                        cursor.execute(f"""
                            ALTER TABLE "{table_name}" 
                            ADD COLUMN "{col}" {dtype};
                        """)
                        logger.info(f"✅ Added column {col} to {table_name}")
    
    def _upsert_rows(self, connection, table_name: str, rows: List[dict]) -> int:
        """Upsert rows into PostgreSQL table."""
        if not rows:
            return 0
        
        # Get all columns from first row (they should all have same structure)
        columns = list(rows[0].keys())
        
        # Prepare SQL
        columns_str = ', '.join([f'"{col}"' for col in columns])
        placeholders = ', '.join(['%s'] * len(columns))
        
        update_set = ', '.join([
            f'"{col}" = EXCLUDED."{col}"' 
            for col in columns 
            if col != "notion_id"
        ])
        
        upsert_sql = f"""
            INSERT INTO "{table_name}" ({columns_str})
            VALUES ({placeholders})
            ON CONFLICT (notion_id)
            DO UPDATE SET {update_set};
        """
        
        # Convert rows to tuples
        records = []
        for row in rows:
            record = []
            for col in columns:
                value = row.get(col)
                
                # Special handling for different value types
                if value == "":
                    # Convert empty strings to None
                    value = None
                elif isinstance(value, str) and col in ["created_time", "last_edited_time"]:
                    # Handle datetime strings
                    if value:  # Only process non-empty values
                        try:
                            # Ensure timezone-aware datetime
                            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                            value = dt
                        except:
                            # If parsing fails, set to None
                            value = None
                elif isinstance(value, str):
                    # Check if it's a date column based on the value format
                    if value and len(value) >= 10 and value[4] == '-' and value[7] == '-':
                        try:
                            # Try to parse as date/datetime
                            if 'T' in value:
                                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                                value = dt
                            else:
                                # Just a date, add time component
                                dt = datetime.fromisoformat(value + "T00:00:00+00:00")
                                value = dt
                        except:
                            # If it fails, keep as string
                            pass
                
                record.append(value)
            records.append(tuple(record))
        
        # Execute in batches
        batch_size = 100
        total_upserted = 0
        
        with connection.cursor() as cursor:
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                try:
                    cursor.executemany(upsert_sql, batch)
                    total_upserted += len(batch)
                    logger.debug(f"Upserted batch {i//batch_size + 1}/{(len(records)-1)//batch_size + 1}")
                except Exception as e:
                    logger.error(f"❌ Error in batch {i//batch_size + 1}: {e}")
                    # Log the problematic record for debugging
                    if batch:
                        logger.debug(f"First record in failed batch: {batch[0]}")
                    raise
        
        return total_upserted
    
    def _get_last_sync_time(self, connection, table_name: str) -> Optional[datetime]:
        """Get the last sync time from the database."""
        try:
            with connection.cursor() as cursor:
                # Check if table exists
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    );
                """, (table_name,))
                
                if not cursor.fetchone()[0]:
                    return None
                
                # Get max last_edited_time
                cursor.execute(f"""
                    SELECT MAX(last_edited_time) 
                    FROM "{table_name}";
                """)
                result = cursor.fetchone()
                
                if result and result[0]:
                    # Ensure timezone awareness
                    dt = result[0]
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                
                return None
        except Exception as e:
            logger.error(f"❌ Error getting last sync time: {e}")
            return None
    
    def sync_database(self, database_config: dict, connection, force_full_sync: bool = False):
        """Sync a single Notion database to Supabase."""
        database_id = database_config["id"]
        database_name = database_config["name"]
        table_name = database_config["supabase_table"]
        
        logger.info(f"🔄 Syncing Notion database '{database_name}' to table '{table_name}'")
        
        # Get last sync time (unless forcing full sync)
        last_sync = None if force_full_sync else self._get_last_sync_time(connection, table_name)
        if last_sync:
            logger.info(f"📅 Last sync: {last_sync.isoformat()}")
        else:
            logger.info("📅 First sync - fetching all pages" if not force_full_sync else "📅 Full sync forced - fetching all pages")
        
        # Get database schema
        schema = self._get_database_schema(database_id)
        if not schema:
            logger.error(f"❌ Failed to get schema for database {database_id}")
            return
        
        # Query pages
        all_rows = []
        has_more = True
        start_cursor = None
        pages_fetched = 0
        
        while has_more:
            result = self._query_database(database_id, start_cursor, last_sync)  # type: ignore
            if not result:
                logger.error(f"❌ Failed to query database {database_id}")
                break
            
            pages = result.get("results", [])
            pages_fetched += len(pages)
            
            # Transform pages to rows
            for page in pages:
                row = self._transform_page_to_row(page, schema)
                all_rows.append(row)
            
            has_more = result.get("has_more", False)
            start_cursor = result.get("next_cursor")
            
            logger.debug(f"Fetched {len(pages)} pages (total: {pages_fetched})")
        
        if not all_rows:
            logger.info(f"✅ No new or updated pages found for '{database_name}'")
            return
        
        logger.info(f"📊 Found {len(all_rows)} new/updated pages")
        
        # Create/alter table
        self._create_or_alter_table(connection, table_name, all_rows)
        
        # Upsert rows
        upserted = self._upsert_rows(connection, table_name, all_rows)
        logger.info(f"✅ Successfully synced {upserted} pages to '{table_name}'")
    
    def run_sync_cycle(self, force_full_sync: bool = False):
        """Run a single sync cycle for all configured databases."""
        logger.info("🚀 Starting sync cycle" + (" (full sync forced)" if force_full_sync else ""))
        
        # Get database connection
        db_config = load_db_config(self.environment)
        connection = get_db_connection(db_config, self.environment)
        
        if not connection:
            logger.error("❌ Failed to connect to database")
            return
        
        try:
            # Sync each database
            synced_count = 0
            for db_config in self.databases:
                try:
                    logger.info(f"{'='*60}")
                    logger.info(f"Database {synced_count + 1}/{len(self.databases)}: {db_config['name']}")
                    logger.info(f"{'='*60}")
                    self.sync_database(db_config, connection, force_full_sync)
                    synced_count += 1
                except Exception as e:
                    logger.error(f"❌ Error syncing database {db_config['name']}: {e}")
                    continue
            
            logger.info(f"\n✅ Sync cycle completed - {synced_count}/{len(self.databases)} databases synced successfully")
        finally:
            connection.close()
    
    def run_continuous(self, force_full_sync_first: bool = False):
        """Run continuous sync with polling."""
        logger.info(f"🔄 Starting continuous sync (polling every {self.poll_every}s)")
        
        first_run = True
        while True:
            try:
                self.run_sync_cycle(force_full_sync=force_full_sync_first and first_run)
                first_run = False
            except Exception as e:
                logger.error(f"❌ Error in sync cycle: {e}")
            
            logger.info(f"💤 Sleeping for {self.poll_every} seconds...")
            time.sleep(self.poll_every)


def main():
    """Main function to run the sync."""
    parser = argparse.ArgumentParser(description="Sync Notion databases to Supabase")
    parser.add_argument("--environment", choices=["local", "cloud"], default="cloud",
                        help="Database environment to use (default: cloud)")
    parser.add_argument("--config", type=str, help="Path to configuration file")
    parser.add_argument("--database-list", type=str, help="Path to database list JSON file")
    parser.add_argument("--once", action="store_true", 
                        help="Run sync once and exit (default: continuous)")
    parser.add_argument("--full-sync", action="store_true",
                        help="Force a full sync, ignoring last sync time")
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    try:
        # Initialize sync
        sync = NotionSupabaseSync(
            config_path=args.config, 
            environment=args.environment,
            database_list_path=args.database_list
        )
        
        if args.once:
            # Run single sync cycle
            sync.run_sync_cycle(force_full_sync=args.full_sync)
        else:
            # Run continuous sync
            sync.run_continuous(force_full_sync_first=args.full_sync)
    
    except KeyboardInterrupt:
        logger.info("⏹️  Sync stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
