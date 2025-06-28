import json
import logging
import sys
import argparse
from pathlib import Path
import os
from notion_client import Client
from datetime import datetime

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

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
    """Search for a row in the database where the 'date' field matches the target date."""
    try:
        logger.debug(f"🔍 Searching for date: {target_date}")
        
        # Format the database ID if needed
        formatted_id = format_database_id(database_id)
        
        # Convert the input date (YYYYMMDD) to ISO format (YYYY-MM-DD)
        date_obj = datetime.strptime(target_date, "%Y%m%d")
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
            logger.warning(f"⚠️ No rows found for date: {target_date}")
            return None
        
        if len(results) > 1:
            logger.warning(f"⚠️ Multiple rows found for date: {target_date}. Using the first one.")
        
        logger.info(f"✅ Found row for date: {target_date}")
        return results[0]
    
    except Exception as e:
        logger.error(f"❌ Error searching by date: {e}")
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
    update_fields = notion_cfg.get("update_fields", [])
    return api_token, databases, update_fields

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Search and update Notion database entries by date.')
    
    parser.add_argument('date', type=str, help='Date to search for (format: YYYYMMDD)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--database-id', type=str, help='Override database ID from config')
    
    return parser.parse_args()

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
    api_token, databases, update_fields = load_notion_config()
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
    
    # Search for the row with the matching date
    matched_row = search_by_date(notion, database_id, args.date)
    if matched_row is None:
        logger.error(f"❌ No row found for date: {args.date}")
        return
    
    # Extract page_id and properties
    page_id = matched_row.get('id', '')
    properties = matched_row.get('properties', {})
    
    # Extract the 'next' field
    next_relation = properties.get('next', {}).get('relation', [])
    next_page_id = next_relation[0].get('id', '') if next_relation else None
    
    if not next_page_id:
        logger.warning("⚠️ No 'next' relation found for this row")
    
    # Extract additional fields from config
    logger.info(f"📋 Extracting fields: {update_fields}")
    extracted_fields = extract_fields(matched_row, update_fields)
    
    # Print summary of original row
    print("\n" + "="*60)
    print("📊 ORIGINAL ROW SUMMARY")
    print("="*60)
    print(f"Page ID: {page_id}")
    print(f"Date: {args.date}")
    print(f"Next Page ID: {next_page_id if next_page_id else 'None'}")
    print("\nExtracted Fields:")
    for field_name, value in extracted_fields.items():
        print(f"  - {field_name}: {value}")
    
    # If there's a next page, retrieve its information
    if next_page_id:
        next_page = get_page_by_id(notion, next_page_id)
        if next_page:
            next_properties = next_page.get('properties', {})
            next_date_prop = next_properties.get('date', {})
            next_date_value = extract_property_value(next_date_prop)
            
            # Extract day field (title) from next page
            next_day_prop = next_properties.get('day', {})
            next_day_value = extract_property_value(next_day_prop)
            
            print("\n" + "="*60)
            print("📅 NEXT DAY SUMMARY")
            print("="*60)
            print(f"Page ID: {next_page_id}")
            print(f"Date: {next_date_value}")
            print(f"Day (title): {next_day_value}")
        else:
            logger.error("❌ Failed to retrieve next page")
    
    print("\n" + "="*60)
    logger.info("✅ Notion Update Process completed")

if __name__ == "__main__":
    main()
