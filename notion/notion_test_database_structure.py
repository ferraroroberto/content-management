import json
import logging
import sys
from pathlib import Path
import os
from notion_client import Client
import pandas as pd
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
    logger = setup_logger("notion_database_structure", file_logging=False, level=log_level)
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

def get_database_structure(notion, database_id):
    """Retrieve the structure of a Notion database."""
    try:
        logger.debug(f"📊 Retrieving database structure for ID: {database_id}")
        
        # Format the database ID if needed
        formatted_id = format_database_id(database_id)
        
        # Retrieve database metadata
        database = notion.databases.retrieve(database_id=formatted_id)
        
        # Extract database properties (columns)
        properties = database.get('properties', {})
        
        # Create a structured representation of the database schema
        schema = {
            "database_id": database_id,
            "title": database.get('title', [{}])[0].get('plain_text', 'Untitled Database'),
            "url": database.get('url', ''),
            "created_time": database.get('created_time', ''),
            "last_edited_time": database.get('last_edited_time', ''),
            "properties": {}
        }
        
        # Process each property to extract its type and configuration
        for prop_name, prop_details in properties.items():
            prop_type = prop_details.get('type', 'unknown')
            
            # Extract specific configuration based on property type
            prop_config = {
                "type": prop_type,
                "id": prop_details.get('id', '')
            }
            
            # Add type-specific details
            if prop_type == 'select':
                options = prop_details.get('select', {}).get('options', [])
                prop_config['options'] = [
                    {
                        "name": opt.get('name', ''),
                        "color": opt.get('color', '')
                    } for opt in options
                ]
            elif prop_type == 'multi_select':
                options = prop_details.get('multi_select', {}).get('options', [])
                prop_config['options'] = [
                    {
                        "name": opt.get('name', ''),
                        "color": opt.get('color', '')
                    } for opt in options
                ]
            elif prop_type == 'relation':
                prop_config['database_id'] = prop_details.get('relation', {}).get('database_id', '')
            elif prop_type == 'formula':
                prop_config['expression'] = prop_details.get('formula', {}).get('expression', '')
            
            schema['properties'][prop_name] = prop_config
        
        logger.info(f"✅ Retrieved database structure with {len(properties)} properties")
        return schema
    
    except Exception as e:
        logger.error(f"❌ Error retrieving database structure: {e}")
        return None

def get_database_content(notion, database_id, max_pages=1):
    """Retrieve the content of a Notion database (limited to 100 records for sampling)."""
    try:
        logger.debug(f"📋 Retrieving sample database content for ID: {database_id} (100 records max)")
        
        # Format the database ID if needed
        formatted_id = format_database_id(database_id)
        
        # Initialize pagination variables
        all_results = []
        
        # Get just one page of results (100 records max)
        response = notion.databases.query(
            database_id=formatted_id,
            page_size=100  # Max allowed by API
        )
        
        results = response.get('results', [])
        all_results.extend(results)
        
        logger.info(f"✅ Retrieved database sample with {len(all_results)} records")
        return all_results
    
    except Exception as e:
        logger.error(f"❌ Error retrieving database content: {e}")
        return None

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
    
    elif prop_type == 'email':
        return property_item.get('email', '')
    
    elif prop_type == 'phone_number':
        return property_item.get('phone_number', '')
    
    elif prop_type == 'formula':
        formula_result = property_item.get('formula', {})
        result_type = formula_result.get('type', '')
        return formula_result.get(result_type, None)
    
    elif prop_type == 'relation':
        relation_array = property_item.get('relation', [])
        return [item.get('id', '') for item in relation_array]
    
    elif prop_type == 'rollup':
        rollup = property_item.get('rollup', {})
        rollup_type = rollup.get('type', '')
        return rollup.get(rollup_type, None)
    
    elif prop_type == 'people':
        people_array = property_item.get('people', [])
        return [item.get('id', '') for item in people_array]
    
    elif prop_type == 'files':
        files_array = property_item.get('files', [])
        return [item.get('name', '') for item in files_array]
    
    elif prop_type == 'created_time':
        return property_item.get('created_time', '')
    
    elif prop_type == 'created_by':
        created_by = property_item.get('created_by', {})
        return created_by.get('id', '')
    
    elif prop_type == 'last_edited_time':
        return property_item.get('last_edited_time', '')
    
    elif prop_type == 'last_edited_by':
        last_edited_by = property_item.get('last_edited_by', {})
        return last_edited_by.get('id', '')
    
    return None

def create_dataframe_from_content(database_content, database_structure):
    """Convert database content to a pandas DataFrame."""
    if not database_content or not database_structure:
        logger.warning("⚠️ Cannot create DataFrame: Missing database content or structure")
        return None
    
    records = []
    properties = database_structure.get('properties', {})
    
    for page in database_content:
        record = {
            'page_id': page.get('id', ''),
            'created_time': page.get('created_time', ''),
            'last_edited_time': page.get('last_edited_time', '')
        }
        
        page_properties = page.get('properties', {})
        for prop_name, prop_details in page_properties.items():
            # Extract the value based on property type
            value = extract_property_value(prop_details)
            record[prop_name] = value
        
        records.append(record)
    
    # Create DataFrame
    df = pd.DataFrame(records)
    
    logger.info(f"✅ Created DataFrame with {len(df)} rows and {len(df.columns)} columns")
    return df

def save_database_info(database_structure, database_df, output_dir=None):
    """Save database structure and content to files."""
    if output_dir is None:
        # Create output directory
        project_root = os.path.dirname(os.path.dirname(__file__))
        output_dir = os.path.join(project_root, "notion", "database_sample")
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamp for filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_id = database_structure.get('database_id', 'unknown')
    
    # Get database name (sanitized for filename)
    db_name = database_structure.get('title', 'untitled').replace(' ', '_').lower()
    sanitized_name = ''.join(c if c.isalnum() or c in ['_', '-'] else '_' for c in db_name)
    
    # Save structure as JSON (overwrite if exists)
    structure_file = os.path.join(output_dir, f"{sanitized_name}_{db_id}_structure.json")
    with open(structure_file, 'w') as f:
        json.dump(database_structure, f, indent=2)
    
    # Save content as CSV only (overwrite if exists)
    if database_df is not None and not database_df.empty:
        csv_file = os.path.join(output_dir, f"{sanitized_name}_{db_id}_content.csv")
        database_df.to_csv(csv_file, index=False)
        logger.info(f"💾 Saved database sample content to CSV: {os.path.basename(csv_file)}")
    
    logger.info(f"💾 Saved database structure to JSON: {os.path.basename(structure_file)}")
    return structure_file

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "config.json")

def load_notion_config():
    """Load Notion parameters from config.json."""
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    notion_cfg = config.get("notion", {})
    api_token = notion_cfg.get("api_token")
    databases = notion_cfg.get("databases", [])
    return api_token, databases

def main():
    """Main function to execute the Notion database structure retrieval."""
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode? (y/n): ").lower()
    debug_mode = debug_input == 'y'
    
    # Configure logger with appropriate level
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Notion Database Structure Retriever")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load Notion config from JSON
    api_token, databases = load_notion_config()
    if not api_token or not databases:
        logger.error("❌ Notion API token or databases not found in config.json")
        return

    # Print all database IDs and names for user reference
    print("\nDatabases found in config.json:")
    for db in databases:
        print(f"  - ID: {db['id']} | Name: {db.get('name', 'UNKNOWN')}")
    print()

    # Initialize Notion client
    notion = init_notion_client(api_token)
    if notion is None:
        logger.error("❌ Failed to initialize Notion client")
        return

    # Iterate over all databases in config
    for db in databases:
        database_id = db["id"]
        database_name = db.get("name", "UNKNOWN")
        logger.info(f"\n=== Processing database: {database_name} (ID: {database_id}) ===")

        # Retrieve database structure
        structure = get_database_structure(notion, database_id)
        if structure is None:
            logger.error("❌ Failed to retrieve database structure")
            continue

        # Print database details
        logger.info(f"📊 Database: {structure.get('title', 'Unknown')}")
        logger.info(f"🔗 URL: {structure.get('url', 'Unknown')}")
        logger.info(f"📋 Properties: {len(structure.get('properties', {}))}")

        if debug_mode:
            # Print properties in debug mode
            for prop_name, prop_details in structure.get('properties', {}).items():
                logger.debug(f"  - {prop_name} ({prop_details.get('type', 'unknown')})")

        # Retrieve sample database content (100 records max)
        content = get_database_content(notion, database_id)
        if content is None:
            logger.error("❌ Failed to retrieve database content")
            continue

        logger.info(f"📋 Retrieved {len(content)} sample records for analysis")

        # Create DataFrame from content
        df = create_dataframe_from_content(content, structure)
        if df is None:
            logger.warning("⚠️ Failed to create DataFrame from database content")
        else:
            if debug_mode:
                print("\n📊 DataFrame Preview:")
                print(df.head())
                print("\n📈 DataFrame Info:")
                print(df.info())

        # Save database information
        save_database_info(structure, df)

        logger.info("✅ Notion Database Structure Retrieval completed for this database")

if __name__ == "__main__":
    main()
