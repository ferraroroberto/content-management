import json
import os
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
import requests
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger - will use existing logger if available
logger = logging.getLogger("notion_database_list")
if not logger.handlers:
    logger = setup_logger("notion_database_list", file_logging=False)


class NotionDatabaseLister:
    """List all Notion databases and save to JSON file."""
    
    def __init__(self, config_path: str = None):
        """Initialize with configuration."""
        self.config = self._load_config(config_path)
        self.notion_token = self.config["notion"]["api_token"]
        self.headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        
    def _load_config(self, config_path: str = None) -> dict:
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
    
    def _search_databases(self, start_cursor: str = None) -> Dict[str, Any]:
        """Search for all databases using Notion API."""
        url = "https://api.notion.com/v1/search"
        
        data = {
            "filter": {
                "value": "database",
                "property": "object"
            },
            "page_size": 100
        }
        
        if start_cursor:
            data["start_cursor"] = start_cursor
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Notion API error: {e}")
            return None
    
    def _normalize_table_name(self, name: str) -> str:
        """Normalize database name to valid PostgreSQL table name."""
        # Convert to lowercase and replace spaces/special chars with underscores
        normalized = name.lower().strip()
        normalized = "".join(c if c.isalnum() or c == "_" else "_" for c in normalized)
        # Remove consecutive underscores
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        # Ensure it doesn't start with a number
        if normalized and normalized[0].isdigit():
            normalized = f"table_{normalized}"
        # Add prefix to avoid conflicts
        normalized = f"notion_{normalized}" if normalized else "notion_untitled"
        return normalized
    
    def get_all_databases(self) -> List[Dict[str, str]]:
        """Get all databases from Notion."""
        logger.info("🔍 Searching for all Notion databases...")
        
        all_databases = []
        has_more = True
        start_cursor = None
        
        while has_more:
            result = self._search_databases(start_cursor)
            if not result:
                logger.error("❌ Failed to search databases")
                break
            
            databases = result.get("results", [])
            
            for db in databases:
                if db.get("object") == "database":
                    # Extract database ID and title
                    db_id = db.get("id", "").replace("-", "")  # Remove hyphens from ID
                    
                    # Get title from title array
                    title_array = db.get("title", [])
                    db_name = ""
                    if title_array:
                        db_name = "".join([t.get("plain_text", "") for t in title_array])
                    
                    if not db_name:
                        db_name = "Untitled Database"
                    
                    database_info = {
                        "id": db_id,
                        "name": db_name,
                        "url": f"https://www.notion.so/robertoferraro/{db_id}",
                        "replication": True,  # Default value
                        "supabase_table": self._normalize_table_name(db_name)  # Default based on name
                    }
                    
                    all_databases.append(database_info)
                    logger.debug(f"Found database: {db_name} ({db_id})")
            
            has_more = result.get("has_more", False)
            start_cursor = result.get("next_cursor")
            
            logger.info(f"📊 Found {len(databases)} databases in this batch (total: {len(all_databases)})")
        
        return all_databases
    
    def load_existing_json(self, file_path: str) -> Dict[str, Dict]:
        """Load existing JSON file and return as dict keyed by ID."""
        if not os.path.exists(file_path):
            logger.info("📄 No existing database list found, creating new one")
            return {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                existing_list = json.load(f)
            
            # Convert to dict keyed by ID for easier lookup
            existing_dict = {db['id']: db for db in existing_list}
            logger.info(f"📄 Loaded {len(existing_dict)} existing databases from JSON")
            return existing_dict
        except Exception as e:
            logger.error(f"❌ Error loading existing JSON: {e}")
            return {}
    
    def merge_with_existing(self, new_databases: List[Dict], existing_dict: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, Any]]:
        """Merge new database list with existing, preserving replication settings."""
        merged_databases = []
        stats = {
            "total": 0,
            "new": [],
            "deleted": [],
            "updated": []
        }
        
        # Track which existing databases we've seen
        seen_ids = set()
        
        # Process new databases
        for new_db in new_databases:
            db_id = new_db['id']
            seen_ids.add(db_id)
            
            if db_id in existing_dict:
                # Existing database - preserve replication and supabase_table settings
                existing_db = existing_dict[db_id]
                merged_db = new_db.copy()
                merged_db['replication'] = existing_db.get('replication', True)
                merged_db['supabase_table'] = existing_db.get('supabase_table', new_db['supabase_table'])
                
                # Check if name changed
                if existing_db.get('name') != new_db['name']:
                    stats['updated'].append({
                        'id': db_id,
                        'old_name': existing_db.get('name'),
                        'new_name': new_db['name']
                    })
                    logger.info(f"📝 Updated name: '{existing_db.get('name')}' → '{new_db['name']}'")
                
                merged_databases.append(merged_db)
            else:
                # New database
                merged_databases.append(new_db)
                stats['new'].append({
                    'id': db_id,
                    'name': new_db['name']
                })
                logger.info(f"✨ New database: {new_db['name']} ({db_id})")
        
        # Check for deleted databases
        for existing_id, existing_db in existing_dict.items():
            if existing_id not in seen_ids:
                stats['deleted'].append({
                    'id': existing_id,
                    'name': existing_db.get('name', 'Unknown')
                })
                logger.info(f"🗑️ Deleted database: {existing_db.get('name', 'Unknown')} ({existing_id})")
        
        stats['total'] = len(merged_databases)
        return merged_databases, stats
    
    def save_to_json(self, databases: List[Dict[str, str]], output_path: str = None):
        """Save databases list to JSON file."""
        if output_path is None:
            output_path = Path(__file__).parent / "notion_database_list.json"
        
        logger.info(f"💾 Saving {len(databases)} databases to {output_path}")
        
        # Sort databases by name for easier reading
        sorted_databases = sorted(databases, key=lambda x: x["name"].lower())
        
        # Save with pretty formatting
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_databases, f, ensure_ascii=False, indent=4)
        
        logger.info(f"✅ Successfully saved {len(databases)} databases to {output_path}")
    
    def run(self, output_path: str = None):
        """Run the database listing process."""
        logger.info("🚀 Starting Notion database listing")
        
        if output_path is None:
            output_path = Path(__file__).parent / "notion_database_list.json"
        
        # Load existing JSON
        existing_dict = self.load_existing_json(output_path)
        
        # Get all databases from Notion
        new_databases = self.get_all_databases()
        
        if not new_databases:
            logger.warning("⚠️ No databases found in Notion")
            return
        
        # Merge with existing
        merged_databases, stats = self.merge_with_existing(new_databases, existing_dict)
        
        # Log summary
        logger.info(f"📊 Database Sync Summary:")
        logger.info(f"  Total databases: {stats['total']}")
        logger.info(f"  New databases: {len(stats['new'])}")
        logger.info(f"  Deleted databases: {len(stats['deleted'])}")
        logger.info(f"  Updated names: {len(stats['updated'])}")
        
        # Save to JSON
        self.save_to_json(merged_databases, output_path)
        
        # Log detailed summary
        if stats['new'] or stats['deleted'] or stats['updated']:
            logger.info("\n📋 Detailed Changes:")
            
            if stats['new']:
                logger.info("  New databases:")
                for db in stats['new']:
                    logger.info(f"    + {db['name']} ({db['id']})")
            
            if stats['deleted']:
                logger.info("  Deleted databases:")
                for db in stats['deleted']:
                    logger.info(f"    - {db['name']} ({db['id']})")
            
            if stats['updated']:
                logger.info("  Updated names:")
                for db in stats['updated']:
                    logger.info(f"    ~ {db['old_name']} → {db['new_name']} ({db['id']})")
        else:
            logger.info("✅ No changes detected")


def main():
    """Main function to run the database lister."""
    parser = argparse.ArgumentParser(description="List all Notion databases and save to JSON")
    parser.add_argument("--config", type=str, help="Path to configuration file")
    parser.add_argument("--output", type=str, help="Output JSON file path (default: notion_database_list.json)")
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    try:
        # Initialize lister
        lister = NotionDatabaseLister(config_path=args.config)
        
        # Run the listing process
        lister.run(output_path=args.output)
    
    except KeyboardInterrupt:
        logger.info("⏹️ Process stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
