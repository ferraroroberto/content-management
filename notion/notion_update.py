import json
import logging
import sys
import argparse
from pathlib import Path
import os
from notion_client import Client
from datetime import datetime, timedelta
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger
from process.supabase_uploader import get_db_connection

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("notion_update", file_logging=False, level=log_level)
    return logger

def init_notion_client(api_token):
    """Initialize Notion Client using the provided API token."""
    logger.debug("🔑 Initializing Notion client")
    try:
        client = Client(auth=api_token)
        logger.info("✅ Notion client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"❌ Error initializing Notion client: {e}")
        return None

def format_database_id(database_id):
    """Format database ID with hyphens if needed."""
    if len(database_id) == 32:
        # Insert hyphens to convert into UUID format
        return f"{database_id[:8]}-{database_id[8:12]}-{database_id[12:16]}-{database_id[16:20]}-{database_id[20:]}"
    return database_id

def extract_property_value(property_item):
    """Extract the value from a property item based on its type."""
    prop_type = property_item.get('type', 'unknown')
    
    if prop_type == 'title':
        title_array = property_item.get('title', [])
        if title_array:
            return title_array[0].get('plain_text', '')
        return ''
    
    elif prop_type == 'rich_text':
        text_array = property_item.get('rich_text', [])
        if text_array:
            return text_array[0].get('plain_text', '')
        return ''
    
    elif prop_type == 'number':
        return property_item.get('number', None)
    
    elif prop_type == 'select':
        select_obj = property_item.get('select', {})
        return select_obj.get('name', '') if select_obj else ''
    
    elif prop_type == 'multi_select':
        multi_select = property_item.get('multi_select', [])
        return [item.get('name', '') for item in multi_select]
    
    elif prop_type == 'date':
        date_obj = property_item.get('date', {})
        if date_obj:
            start = date_obj.get('start', '')
            end = date_obj.get('end', '')
            return {'start': start, 'end': end} if end else start
        return None
    
    elif prop_type == 'checkbox':
        return property_item.get('checkbox', False)
    
    elif prop_type == 'url':
        return property_item.get('url', '')
    
    elif prop_type == 'relation':
        relation_array = property_item.get('relation', [])
        return [item.get('id', '') for item in relation_array]
    
    elif prop_type == 'formula':
        formula_result = property_item.get('formula', {})
        result_type = formula_result.get('type', '')
        return formula_result.get(result_type, None)
    
    return None

def search_by_date(notion, database_id, target_date):
    """Search for a row in the database where the 'date' field matches the day before target date."""
    try:
        # Convert the input date (YYYYMMDD) to a datetime object
        date_obj = datetime.strptime(target_date, "%Y%m%d")
        
        # Calculate previous day
        prev_date_obj = date_obj - timedelta(days=1)
        prev_date_str = prev_date_obj.strftime("%Y%m%d")
        
        logger.debug(f"🔍 Searching for previous date: {prev_date_str} (day before {target_date})")
        
        # Format the database ID if needed
        formatted_id = format_database_id(database_id)
        
        # Convert to ISO format (YYYY-MM-DD)
        iso_date = prev_date_obj.strftime("%Y-%m-%d")
        
        # Query the database with date filter
        response = notion.databases.query(
            database_id=formatted_id,
            filter={
                "property": "date",
                "date": {
                    "equals": iso_date
                }
            }
        )
        
        results = response.get('results', [])
        
        if not results:
            logger.warning(f"⚠️ No rows found for previous date: {prev_date_str}")
            return None
        
        if len(results) > 1:
            logger.warning(f"⚠️ Multiple rows found for previous date: {prev_date_str}. Using the first one.")
        
        logger.info(f"✅ Found row for previous date: {prev_date_str}")
        return results[0]
    
    except Exception as e:
        logger.error(f"❌ Error searching by date: {e}")
        return None

def search_by_current_date(notion, database_id, target_date):
    """Search for a row in the database where the 'date' field matches the target date."""
    try:
        # Convert the input date (YYYYMMDD) to a datetime object
        date_obj = datetime.strptime(target_date, "%Y%m%d")
        
        logger.debug(f"🔍 Searching for current date: {target_date}")
        
        # Format the database ID if needed
        formatted_id = format_database_id(database_id)
        
        # Convert to ISO format (YYYY-MM-DD)
        iso_date = date_obj.strftime("%Y-%m-%d")
        
        # Query the database with date filter
        response = notion.databases.query(
            database_id=formatted_id,
            filter={
                "property": "date",
                "date": {
                    "equals": iso_date
                }
            }
        )
        
        results = response.get('results', [])
        
        if not results:
            logger.warning(f"⚠️ No rows found for current date: {target_date}")
            return None
        
        if len(results) > 1:
            logger.warning(f"⚠️ Multiple rows found for current date: {target_date}. Using the first one.")
        
        logger.info(f"✅ Found row for current date: {target_date}")
        return results[0]
    
    except Exception as e:
        logger.error(f"❌ Error searching by current date: {e}")
        return None

def get_page_by_id(notion, page_id):
    """Retrieve a page by its ID."""
    try:
        logger.debug(f"📄 Retrieving page with ID: {page_id}")
        
        page = notion.pages.retrieve(page_id=page_id)
        
        logger.info(f"✅ Retrieved page: {page_id}")
        return page
    
    except Exception as e:
        logger.error(f"❌ Error retrieving page: {e}")
        return None

def extract_fields(page, fields_to_extract):
    """Extract specified fields from a page."""
    extracted = {}
    properties = page.get('properties', {})
    
    for field_name in fields_to_extract:
        if field_name in properties:
            prop_value = extract_property_value(properties[field_name])
            extracted[field_name] = prop_value
        else:
            logger.warning(f"⚠️ Field '{field_name}' not found in page properties")
            extracted[field_name] = None
    
    return extracted

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "config.json")

def load_notion_config():
    """Load Notion parameters from config.json."""
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    notion_cfg = config.get("notion", {})
    api_token = notion_cfg.get("api_token")
    databases = notion_cfg.get("databases", [])
    
    # Load separate field configurations
    update_fields_followers = notion_cfg.get("update_fields_followers", [])
    update_fields_posts = notion_cfg.get("update_fields_posts", [])
    update_field_mapping_followers = notion_cfg.get("update_field_mapping_followers", {})
    update_field_mapping_posts = notion_cfg.get("update_field_mapping_posts", {})
    
    # Get Supabase table names
    supabase_cfg = config.get("supabase", {})
    posts_table = supabase_cfg.get("posts_table", "posts")
    profile_table = supabase_cfg.get("profile_table", "profile")
    
    return (api_token, databases, update_fields_followers, update_fields_posts, 
            update_field_mapping_followers, update_field_mapping_posts, 
            posts_table, profile_table)

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Search and update Notion database entries by date.')
    
    parser.add_argument('date', type=str, help='Date to search for (format: YYYYMMDD)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--database-id', type=str, help='Override database ID from config')
    
    return parser.parse_args()

def get_supabase_data(connection, date_str, posts_table, profile_table):
    """Get data from Supabase for the specified date."""
    try:
        # Convert date format from YYYYMMDD to YYYY-MM-DD
        date_obj = datetime.strptime(date_str, "%Y%m%d")
        formatted_date = date_obj.strftime("%Y-%m-%d")
        
        cursor = connection.cursor()
        
        # Get posts data from the CURRENT day (no lag)
        logger.debug(f"🔍 Querying {posts_table} for current date: {formatted_date}")
        posts_query = f"SELECT * FROM {posts_table} WHERE date = %s LIMIT 1"
        cursor.execute(posts_query, (formatted_date,))
        posts_columns = [desc[0] for desc in cursor.description]
        posts_row = cursor.fetchone()
        posts_data = dict(zip(posts_columns, posts_row)) if posts_row else None
        
        # Get profile data from the CURRENT day
        logger.debug(f"🔍 Querying {profile_table} for current date: {formatted_date}")
        profile_query = f"SELECT * FROM {profile_table} WHERE date = %s LIMIT 1"
        cursor.execute(profile_query, (formatted_date,))
        profile_columns = [desc[0] for desc in cursor.description]
        profile_row = cursor.fetchone()
        profile_data = dict(zip(profile_columns, profile_row)) if profile_row else None
        
        cursor.close()
        
        if posts_data:
            logger.info(f"✅ Found posts data for current date: {formatted_date}")
        else:
            logger.warning(f"⚠️ No posts data found for current date: {formatted_date}")
            
        if profile_data:
            logger.info(f"✅ Found profile data for current date: {formatted_date}")
        else:
            logger.warning(f"⚠️ No profile data found for current date: {formatted_date}")
        
        return posts_data, profile_data
        
    except Exception as e:
        logger.error(f"❌ Error getting Supabase data: {e}")
        return None, None

def map_supabase_to_notion_fields(posts_data, profile_data, field_mapping):
    """Map Supabase fields to Notion fields based on the mapping configuration."""
    mapped_data = {}
    
    for notion_field, supabase_field in field_mapping.items():
        if supabase_field is None:
            mapped_data[notion_field] = None
            continue
            
        # Parse the table and field name
        if '.' in supabase_field:
            table, field = supabase_field.split('.', 1)
            
            if table == 'posts' and posts_data and field in posts_data:
                mapped_data[notion_field] = posts_data[field]
            elif table == 'profile' and profile_data and field in profile_data:
                mapped_data[notion_field] = profile_data[field]
            else:
                mapped_data[notion_field] = None
                logger.debug(f"Field {supabase_field} not found in data")
        else:
            mapped_data[notion_field] = None
            logger.warning(f"Invalid field mapping format: {supabase_field}")
    
    return mapped_data

def prepare_notion_update(property_type, value):
    """Prepare the update payload for a Notion property based on its type."""
    if value is None:
        return None
    
    # Handle datetime objects
    if isinstance(value, datetime) or hasattr(value, 'isoformat'):
        # Format as ISO string YYYY-MM-DD
        try:
            value_str = value.isoformat().split('T')[0]
            
            if property_type == 'date':
                return {"date": {"start": value_str}}
            elif property_type == 'number':
                return {"number": None}  # Cannot convert date to number
            else:
                # For other types, use the string representation
                value = value_str
        except AttributeError:
            logger.warning(f"⚠️ Could not format date value: {value}")
    
    if property_type == 'number':
        try:
            return {"number": float(value) if value is not None else None}
        except (ValueError, TypeError):
            logger.warning(f"⚠️ Could not convert value '{value}' of type {type(value).__name__} to number")
            return {"number": None}
    elif property_type == 'rich_text':
        return {"rich_text": [{"text": {"content": str(value)}}] if value else []}
    elif property_type == 'url':
        return {"url": str(value) if value else None}
    elif property_type == 'title':
        return {"title": [{"text": {"content": str(value)}}] if value else []}
    elif property_type == 'date':
        # Handle date type properly
        if isinstance(value, str):
            return {"date": {"start": value}}
        else:
            return {"date": {"start": str(value)}}
    else:
        # Default to rich_text for unknown types
        return {"rich_text": [{"text": {"content": str(value)}}] if value else []}

def update_notion_page(notion, page_id, updates):
    """Update a Notion page with the provided field updates."""
    try:
        logger.debug(f"📝 Updating Notion page: {page_id}")
        
        # Filter out None values
        properties = {k: v for k, v in updates.items() if v is not None}
        
        if not properties:
            logger.warning("⚠️ No properties to update")
            return None
            
        response = notion.pages.update(
            page_id=page_id,
            properties=properties
        )
        
        logger.info(f"✅ Successfully updated Notion page")
        return response
        
    except Exception as e:
        logger.error(f"❌ Error updating Notion page: {e}")
        return None

def create_tracking_table(connection):
    """Create the notion_tracking table if it doesn't exist."""
    try:
        cursor = connection.cursor()
        
        create_table_query = """
        CREATE TABLE IF NOT EXISTS notion_tracking (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notion_page_id TEXT NOT NULL,
            notion_field TEXT NOT NULL,
            notion_value_initial TEXT,
            notion_value_final TEXT
        );
        """
        
        cursor.execute(create_table_query)
        connection.commit()
        cursor.close()
        
        logger.debug("✅ Tracking table created/verified")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating tracking table: {e}")
        connection.rollback()
        return False

def log_field_changes(connection, page_id, changes):
    """Log field changes to the tracking table."""
    try:
        cursor = connection.cursor()
        
        insert_query = """
        INSERT INTO notion_tracking 
        (notion_page_id, notion_field, notion_value_initial, notion_value_final)
        VALUES (%s, %s, %s, %s);
        """
        
        for change in changes:
            cursor.execute(insert_query, (
                page_id,
                change['field'],
                str(change['initial']) if change['initial'] is not None else None,
                str(change['final']) if change['final'] is not None else None
            ))
        
        connection.commit()
        cursor.close()
        
        logger.info(f"✅ Logged {len(changes)} field changes to tracking table")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error logging changes: {e}")
        connection.rollback()
        return False

def main():
    """Main function to execute the Notion update process."""
    # Parse command-line arguments
    args = parse_arguments()
    
    # Configure logger with appropriate level based on args
    debug_mode = args.debug
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Notion Update Process")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    logger.info(f"📅 Target date: {args.date}")
    
    # Load Notion config from JSON
    (api_token, databases, update_fields_followers, update_fields_posts, 
     update_field_mapping_followers, update_field_mapping_posts, 
     posts_table, profile_table) = load_notion_config()
    
    if not api_token or not databases:
        logger.error("❌ Notion API token or databases not found in config.json")
        return
    
    # Get database ID (from args or config)
    if args.database_id:
        database_id = args.database_id
        logger.info(f"📊 Using database ID from command line: {database_id}")
    else:
        # Use the first database from config
        database_id = databases[0]["id"]
        database_name = databases[0].get("name", "UNKNOWN")
        logger.info(f"📊 Using database from config: {database_name} (ID: {database_id})")
    
    # Initialize Notion client
    notion = init_notion_client(api_token)
    if notion is None:
        logger.error("❌ Failed to initialize Notion client")
        return
    
    # Search for the row with the matching date (one day before) for POSTS update
    previous_day_row = search_by_date(notion, database_id, args.date)
    if previous_day_row is None:
        logger.error(f"❌ No row found for the day before date: {args.date}")
        return
    
    # Search for the current day row for FOLLOWERS update
    current_day_row = search_by_current_date(notion, database_id, args.date)
    if current_day_row is None:
        logger.error(f"❌ No row found for current date: {args.date}")
        return
    
    # Extract page_id and properties for both rows
    previous_page_id = previous_day_row.get('id', '')
    previous_properties = previous_day_row.get('properties', {})
    
    current_page_id = current_day_row.get('id', '')
    current_properties = current_day_row.get('properties', {})
    
    # Get previous day date for display
    date_obj = datetime.strptime(args.date, "%Y%m%d")
    prev_date_obj = date_obj - timedelta(days=1)
    prev_date_str = prev_date_obj.strftime("%Y%m%d")
    
    # Extract the 'next' field from previous day
    next_relation = previous_properties.get('next', {}).get('relation', [])
    next_page_id = next_relation[0].get('id', '') if next_relation else None
    
    if not next_page_id:
        logger.warning("⚠️ No 'next' relation found for previous day row")
    
    # Extract fields for previous day (posts fields)
    logger.info(f"📋 Number of posts fields to extract: {len(update_fields_posts)}")
    extracted_posts_fields = extract_fields(previous_day_row, update_fields_posts)
    
    # Extract fields for current day (follower fields)
    logger.info(f"📋 Number of follower fields to extract: {len(update_fields_followers)}")
    extracted_followers_fields = extract_fields(current_day_row, update_fields_followers)
    
    # Print summary of both rows
    print("\n" + "="*60)
    print("📊 PREVIOUS DAY ROW SUMMARY (FOR POSTS UPDATE)")
    print("="*60)
    print(f"Page ID: {previous_page_id}")
    print(f"Date: {prev_date_str}")
    print("\nPosts Fields to Update:\n")
    for field_name in update_fields_posts:
        value = extracted_posts_fields.get(field_name)
        print(f"  - {field_name}: {value}")
    
    print("\n" + "="*60)
    print("📊 CURRENT DAY ROW SUMMARY (FOR FOLLOWERS UPDATE)")
    print("="*60)
    print(f"Page ID: {current_page_id}")
    print(f"Date: {args.date}")
    print("\nFollower Fields to Update:\n")
    for field_name in update_fields_followers:
        value = extracted_followers_fields.get(field_name)
        print(f"  - {field_name}: {value}")
    print("\n")
    
    # Connect to Supabase
    logger.info("🔌 Connecting to Supabase")
    supabase_connection = get_db_connection()
    if not supabase_connection:
        logger.error("❌ Failed to connect to Supabase")
        return
    
    # Get data from Supabase
    logger.info("📊 Fetching data from Supabase")
    posts_data, profile_data = get_supabase_data(supabase_connection, args.date, posts_table, profile_table)
    
    # Map Supabase fields to Notion fields for both types
    mapped_posts_data = map_supabase_to_notion_fields(posts_data, profile_data, update_field_mapping_posts)
    mapped_followers_data = map_supabase_to_notion_fields(posts_data, profile_data, update_field_mapping_followers)
    
    # Print Supabase data summary
    print("\n" + "="*60)
    print("🗄️  SUPABASE DATA SUMMARY")
    print("="*60)
    print(f"Posts data (previous day): {'Yes' if posts_data else 'No'}")
    print(f"Profile data (current day): {'Yes' if profile_data else 'No'}")
    print("\nMapped Posts Fields:\n")
    for field_name, value in mapped_posts_data.items():
        print(f"  - {field_name}: {value}")
    print("\nMapped Follower Fields:\n")
    for field_name, value in mapped_followers_data.items():
        print(f"  - {field_name}: {value}")
    print("\n")

    # Prepare updates for Notion - POSTS (previous day)
    posts_updates = {}
    posts_changes = []
    
    for field_name in update_fields_posts:
        if field_name in mapped_posts_data:
            new_value = mapped_posts_data[field_name]
            old_value = extracted_posts_fields.get(field_name)
            
            # Get the property type from the original row
            prop = previous_properties.get(field_name, {})
            prop_type = prop.get('type', 'rich_text')
            
            # Prepare the update payload
            update_payload = prepare_notion_update(prop_type, new_value)
            
            if update_payload is not None:
                posts_updates[field_name] = update_payload
                
                # Track the change
                posts_changes.append({
                    'field': field_name,
                    'initial': old_value,
                    'final': new_value
                })
    
    # Prepare updates for Notion - FOLLOWERS (current day)
    followers_updates = {}
    followers_changes = []
    
    for field_name in update_fields_followers:
        if field_name in mapped_followers_data:
            new_value = mapped_followers_data[field_name]
            old_value = extracted_followers_fields.get(field_name)
            
            # Get the property type from the original row
            prop = current_properties.get(field_name, {})
            prop_type = prop.get('type', 'rich_text')
            
            # Prepare the update payload
            update_payload = prepare_notion_update(prop_type, new_value)
            
            if update_payload is not None:
                followers_updates[field_name] = update_payload
                
                # Track the change
                followers_changes.append({
                    'field': field_name,
                    'initial': old_value,
                    'final': new_value
                })
    
    # Update Notion pages
    if posts_updates or followers_updates:
        total_updates = len(posts_updates) + len(followers_updates)
        logger.info(f"📝 Ready to update {total_updates} fields in Notion")
        print(f"\nReady to update:\n- {len(posts_updates)} posts fields on previous day\n- {len(followers_updates)} follower fields on current day")
        print("\nPress Enter to continue with the update or Ctrl+C to cancel...")
        try:
            input()  # Wait for user to press Enter
            
            # Update posts fields on previous day
            if posts_updates:
                logger.info(f"📝 Updating {len(posts_updates)} posts fields on previous day")
                posts_update_result = update_notion_page(notion, previous_page_id, posts_updates)
                
                if posts_update_result:
                    # Create tracking table if needed
                    create_tracking_table(supabase_connection)
                    
                    # Log changes to Supabase
                    log_field_changes(supabase_connection, previous_page_id, posts_changes)
                else:
                    logger.error("❌ Failed to update posts fields on previous day")
            
            # Update follower fields on current day
            if followers_updates:
                logger.info(f"📝 Updating {len(followers_updates)} follower fields on current day")
                followers_update_result = update_notion_page(notion, current_page_id, followers_updates)
                
                if followers_update_result:
                    # Create tracking table if needed
                    create_tracking_table(supabase_connection)
                    
                    # Log changes to Supabase
                    log_field_changes(supabase_connection, current_page_id, followers_changes)
                else:
                    logger.error("❌ Failed to update follower fields on current day")
            
            # Print field update summary
            print("\n" + "="*60)
            print("📝 FIELD UPDATE SUMMARY")
            print("="*60)
            
            if posts_changes:
                print("\nPOSTS UPDATES (Previous Day):")
                for change in posts_changes:
                    print(f"  {change['field']}: {change['initial']} → {change['final']}")
            
            if followers_changes:
                print("\nFOLLOWER UPDATES (Current Day):")
                for change in followers_changes:
                    print(f"  {change['field']}: {change['initial']} → {change['final']}")
                    
        except KeyboardInterrupt:
            logger.info("❌ Update cancelled by user")
            sys.exit(0)
    else:
        logger.info("ℹ️ No fields to update")
    
    # Close Supabase connection
    supabase_connection.close()
    
    print("\n" + "="*60)
    print("\n")

    logger.info("✅ Notion Update Process completed")

if __name__ == "__main__":
    main()
