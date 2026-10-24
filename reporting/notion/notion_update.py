import logging
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional
from notion_client import Client
from psycopg2.extensions import connection as PgConnection

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent.parent))
from config.loader import load_full_config
from config.logger_config import setup_logger
from reporting.process.supabase_uploader import get_db_connection
# init_notion_client / format_database_id / extract_property_value /
# prepare_notion_update live in the shared _client module; re-exported here
# so this module's own callers (and clone_to_other_platforms.py) keep working.
from reporting.notion._client import (
    extract_property_value,
    format_database_id,
    init_notion_client,
    load_notion_block,
    prepare_notion_update,
)

# Set up logger. Module-scope default is a real (unconfigured) Logger — never
# None — so `logger.xxx(...)` calls elsewhere in this module are plain Logger
# calls with no Optional to work around. `configure_logger()` replaces it
# with the fully-configured (handlers + formatter) instance.
logger = logging.getLogger("notion_update")

def configure_logger(debug_mode: bool = False) -> logging.Logger:
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("notion_update", file_logging=False, level=log_level)
    return logger

def parse_date(date_str: str) -> datetime:
    """Parse date string in either YYYY-MM-DD or YYYYMMDD format."""
    try:
        if '-' in date_str:
            return datetime.strptime(date_str, "%Y-%m-%d")
        else:
            return datetime.strptime(date_str, "%Y%m%d")
    except ValueError as e:
        logger.error(f"❌ Error parsing date '{date_str}': {e}")
        raise

def find_row_for_date(notion: Client, database_id: str, iso_date: str) -> Optional[dict[str, Any]]:
    """Query the database for the row whose 'date' field equals `iso_date`
    (YYYY-MM-DD). Single-source lookup shared by `search_by_date`,
    `search_by_current_date`, and the next-day 'next' relation lookup below —
    each previously hand-rolled its own copy of this query."""
    try:
        formatted_id = format_database_id(database_id)
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
            logger.warning(f"⚠️ No rows found for date: {iso_date}")
            return None

        if len(results) > 1:
            logger.warning(f"⚠️ Multiple rows found for date: {iso_date}. Using the first one.")

        logger.info(f"✅ Found row for date: {iso_date}")
        return results[0]

    except Exception as e:
        logger.error(f"❌ Error searching by date: {e}")
        return None

def search_by_date(notion: Client, database_id: str, target_date: str) -> Optional[dict[str, Any]]:
    """Search for a row in the database where the 'date' field matches the day before target date."""
    try:
        # Convert the input date to a datetime object
        date_obj = parse_date(target_date)

        # Calculate previous day
        prev_date_obj = date_obj - timedelta(days=1)
        iso_date = prev_date_obj.strftime("%Y-%m-%d")

        logger.debug(f"🔍 Searching for previous date: {iso_date} (day before {target_date})")

        return find_row_for_date(notion, database_id, iso_date)

    except Exception as e:
        logger.error(f"❌ Error searching by date: {e}")
        return None

def search_by_current_date(notion: Client, database_id: str, target_date: str) -> Optional[dict[str, Any]]:
    """Search for a row in the database where the 'date' field matches the target date."""
    try:
        # Convert the input date to a datetime object
        date_obj = parse_date(target_date)

        iso_date = date_obj.strftime("%Y-%m-%d")

        logger.debug(f"🔍 Searching for current date: {target_date}")

        return find_row_for_date(notion, database_id, iso_date)

    except Exception as e:
        logger.error(f"❌ Error searching by current date: {e}")
        return None

def extract_fields(page: dict[str, Any], fields_to_extract: list[str]) -> dict[str, Any]:
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

def load_notion_config() -> tuple[
    Optional[str], list[dict[str, Any]], list[str], list[str],
    dict[str, Optional[str]], dict[str, Optional[str]], str, str,
]:
    """Load Notion parameters from config.json."""
    notion_cfg = load_notion_block()
    api_token = notion_cfg.get("api_token")
    databases = notion_cfg.get("databases", [])
    
    # Load separate field configurations
    update_fields_followers = notion_cfg.get("update_fields_followers", [])
    update_fields_posts = notion_cfg.get("update_fields_posts", [])
    update_field_mapping_followers = notion_cfg.get("update_field_mapping_followers", {})
    update_field_mapping_posts = notion_cfg.get("update_field_mapping_posts", {})
    
    # Get Supabase table names
    supabase_cfg = load_full_config().get("supabase", {})
    posts_table = supabase_cfg.get("posts_table", "posts")
    profile_table = supabase_cfg.get("profile_table", "profile")
    
    return (api_token, databases, update_fields_followers, update_fields_posts, 
            update_field_mapping_followers, update_field_mapping_posts, 
            posts_table, profile_table)

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Search and update Notion database entries by date.')
    
    parser.add_argument('date', type=str, help='Date to search for (format: YYYY-MM-DD or YYYYMMDD)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--database-id', type=str, help='Override database ID from config')
    parser.add_argument('-y', '--yes', action='store_true',
                        help='Auto-confirm the update prompt (skip the "Press Enter to continue" wait)')

    return parser.parse_args()

def get_supabase_data(
    connection: PgConnection, date_str: str, posts_table: str, profile_table: str,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Get data from Supabase for the specified date.

    Raises on a connection/query failure instead of swallowing it — ``(None,
    None)`` already means "queries ran, no row for this date", a distinct,
    successful outcome from "the read itself failed" (issue #445). Callers
    that want a graceful pipeline-step failure catch around this call.
    """
    # Convert date format
    date_obj = parse_date(date_str)
    formatted_date = date_obj.strftime("%Y-%m-%d")

    cursor = connection.cursor()
    try:
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
    finally:
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

def map_supabase_to_notion_fields(
    posts_data: Optional[dict[str, Any]],
    profile_data: Optional[dict[str, Any]],
    field_mapping: dict[str, Optional[str]],
) -> dict[str, Any]:
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

def update_notion_page(notion: Client, page_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
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

def create_tracking_table(connection: PgConnection) -> bool:
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

def log_field_changes(connection: PgConnection, page_id: str, changes: list[dict[str, Any]]) -> bool:
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

def _lookup_rows(notion, database_id, date_str):
    """Find the previous-day row (posts update) and the current-day row (followers update).

    Returns ``(previous_row, current_row)``, or ``None`` after logging why when either is missing.
    """
    # Search for the row with the matching date (one day before) for POSTS update
    previous_day_row = search_by_date(notion, database_id, date_str)
    if previous_day_row is None:
        logger.error(f"❌ No row found for the day before date: {date_str}")
        return None

    # Search for the current day row for FOLLOWERS update
    current_day_row = search_by_current_date(notion, database_id, date_str)
    if current_day_row is None:
        logger.error(f"❌ No row found for current date: {date_str}")
        return None

    # Extract the 'next' field from previous day
    next_relation = previous_day_row.get('properties', {}).get('next', {}).get('relation', [])
    if not (next_relation[0].get('id', '') if next_relation else None):
        logger.warning("⚠️ No 'next' relation found for previous day row")

    return previous_day_row, current_day_row


def _log_row_summaries(date_str, previous_day_row, current_day_row, update_fields_posts, extracted_posts_fields,
                       update_fields_followers, extracted_followers_fields):
    """Log what is about to be overwritten on both rows."""
    prev_date_str = (parse_date(date_str) - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("PREVIOUS DAY ROW SUMMARY (FOR POSTS UPDATE)")
    logger.info("Page ID: %s  Date: %s", previous_day_row.get('id', ''), prev_date_str)
    logger.info("Posts Fields to Update:")
    for field_name in update_fields_posts:
        value = extracted_posts_fields.get(field_name)
        logger.info("  - %s: %s", field_name, value)

    logger.info("=" * 60)
    logger.info("CURRENT DAY ROW SUMMARY (FOR FOLLOWERS UPDATE)")
    logger.info("Page ID: %s  Date: %s", current_day_row.get('id', ''), date_str)
    logger.info("Follower Fields to Update:")
    for field_name in update_fields_followers:
        value = extracted_followers_fields.get(field_name)
        logger.info("  - %s: %s", field_name, value)


def build_updates(
    fields: list[str], mapped: dict[str, Any], extracted: dict[str, Any], props: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build one row's Notion payload and change log.

    ``fields`` are the configured field names, ``mapped`` the Supabase values keyed by Notion field,
    ``extracted`` the row's current values and ``props`` its raw properties (for each field's type).
    Returns ``(updates, changes)``; a field with no mapping, or whose value yields no payload, is skipped.
    """
    updates = {}
    changes = []
    for field_name in fields:
        if field_name in mapped:
            new_value = mapped[field_name]
            old_value = extracted.get(field_name)

            # Get the property type from the original row
            prop_type = props.get(field_name, {}).get('type', 'rich_text')

            # Prepare the update payload
            update_payload = prepare_notion_update(prop_type, new_value)

            if update_payload is not None:
                updates[field_name] = update_payload

                # Track the change
                changes.append({
                    'field': field_name,
                    'initial': old_value,
                    'final': new_value
                })
    return updates, changes


def _add_next_relation(notion, database_id, date_obj, current_properties, followers_updates, followers_changes):
    """Point the current day row's 'next' relation at tomorrow's row (mutates the followers update + change log)."""
    next_day_iso = (date_obj + timedelta(days=1)).strftime("%Y-%m-%d")
    logger.debug(f"🔍 Looking up next-day row for {next_day_iso}")
    next_day_row = find_row_for_date(notion, database_id, next_day_iso)

    if not next_day_row:
        logger.warning(f"⚠️ No row found for next day ({next_day_iso}); 'next' will be skipped")
        return

    next_day_page_id = next_day_row.get('id', '')
    current_next_relation = current_properties.get('next', {}).get('relation', [])
    current_next_id = current_next_relation[0].get('id', '') if current_next_relation else None

    if current_next_id == next_day_page_id:
        logger.info(f"   'next' already points to {next_day_iso}; skipping")
        return

    next_payload = prepare_notion_update('relation', next_day_page_id)
    if next_payload is not None:
        followers_updates['next'] = next_payload
        followers_changes.append({
            'field': 'next',
            'initial': current_next_id,
            'final': next_day_page_id,
        })
        logger.info(
            "📅 Will set 'next' relation: %s → %s (%s)",
            current_next_id or '(empty)', next_day_page_id, next_day_iso,
        )


def _apply_updates(notion, supabase_connection, auto_confirm, posts_target, followers_target):
    """Write both rows to Notion and log each successful write to Supabase.

    ``posts_target`` / ``followers_target`` are ``(page_id, updates, changes)``. Returns ``False`` when a page
    update fails (the other row is still attempted).
    """
    posts_page_id, posts_updates, posts_changes = posts_target
    followers_page_id, followers_updates, followers_changes = followers_target
    update_ok = True
    if posts_updates or followers_updates:
        total_updates = len(posts_updates) + len(followers_updates)
        logger.info("Ready to update %d fields in Notion", total_updates)
        logger.info("Ready to update: %d posts fields on previous day, %d follower fields on current day", len(posts_updates), len(followers_updates))
        try:
            if auto_confirm:
                logger.info("✅ Auto-confirm enabled (--yes); proceeding with update")
            else:
                print("\nPress Enter to continue with the update or Ctrl+C to cancel...")
                input()  # Wait for user to press Enter

            for what, page_id, updates, changes in (
                ("posts fields on previous day", posts_page_id, posts_updates, posts_changes),
                ("follower fields on current day", followers_page_id, followers_updates, followers_changes),
            ):
                if not updates:
                    continue
                logger.info(f"📝 Updating {len(updates)} {what}")
                if update_notion_page(notion, page_id, updates):
                    # Create tracking table if needed
                    create_tracking_table(supabase_connection)

                    # Log changes to Supabase
                    log_field_changes(supabase_connection, page_id, changes)
                else:
                    logger.error(f"❌ Failed to update {what}")
                    update_ok = False

            # Print field update summary
            logger.info("=" * 60)
            logger.info("FIELD UPDATE SUMMARY")

            if posts_changes:
                logger.info("POSTS UPDATES (Previous Day):")
                for change in posts_changes:
                    logger.info("  %s: %s → %s", change['field'], change['initial'], change['final'])

            if followers_changes:
                logger.info("FOLLOWER UPDATES (Current Day):")
                for change in followers_changes:
                    logger.info("  %s: %s → %s", change['field'], change['initial'], change['final'])

        except KeyboardInterrupt:
            logger.info("❌ Update cancelled by user")
            sys.exit(0)
    else:
        logger.info("ℹ️ No fields to update")
    return update_ok


def main() -> bool:
    """Main function to execute the Notion update process.

    Returns ``True``/``False`` so ``reporting_pipeline.run_module`` can record
    a step failure on any of the early bail-outs below (missing token, failed
    client init, no editorial row, no Supabase connection) or a failed page
    update — all of which previously returned silently on a green "completed
    successfully" log line one level up (issue #246).
    """
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
        return False

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
        return False

    # Lookup: the rows to update
    rows = _lookup_rows(notion, database_id, args.date)
    if rows is None:
        return False
    previous_day_row, current_day_row = rows
    previous_properties = previous_day_row.get('properties', {})
    current_properties = current_day_row.get('properties', {})

    # Extract fields for previous day (posts fields) and current day (follower fields)
    logger.info(f"📋 Number of posts fields to extract: {len(update_fields_posts)}")
    extracted_posts_fields = extract_fields(previous_day_row, update_fields_posts)
    logger.info(f"📋 Number of follower fields to extract: {len(update_fields_followers)}")
    extracted_followers_fields = extract_fields(current_day_row, update_fields_followers)

    _log_row_summaries(args.date, previous_day_row, current_day_row, update_fields_posts, extracted_posts_fields,
                       update_fields_followers, extracted_followers_fields)

    # Connect to Supabase
    logger.info("🔌 Connecting to Supabase")
    supabase_connection = get_db_connection()
    if not supabase_connection:
        logger.error("❌ Failed to connect to Supabase")
        return False

    # Get data from Supabase
    logger.info("📊 Fetching data from Supabase")
    try:
        posts_data, profile_data = get_supabase_data(supabase_connection, args.date, posts_table, profile_table)
    except Exception as e:
        # Distinct from "query ran, no row for this date" (logged as a warning
        # below, in get_supabase_data): the read itself failed, so this step
        # must not report green (issue #445).
        logger.error(f"❌ Failed to read Supabase data for {args.date}: {e}")
        supabase_connection.close()
        return False

    # Map Supabase fields to Notion fields for both types
    mapped_posts_data = map_supabase_to_notion_fields(posts_data, profile_data, update_field_mapping_posts)
    mapped_followers_data = map_supabase_to_notion_fields(posts_data, profile_data, update_field_mapping_followers)

    # Print Supabase data summary
    logger.info("=" * 60)
    logger.info("SUPABASE DATA SUMMARY")
    logger.info("Posts data (previous day): %s", "Yes" if posts_data else "No")
    logger.info("Profile data (current day): %s", "Yes" if profile_data else "No")
    logger.info("Mapped Posts Fields:")
    for field_name, value in mapped_posts_data.items():
        logger.info("  - %s: %s", field_name, value)
    logger.info("Mapped Follower Fields:")
    for field_name, value in mapped_followers_data.items():
        logger.info("  - %s: %s", field_name, value)

    # Build: POSTS (previous day) and FOLLOWERS (current day, plus its 'next' relation to tomorrow)
    posts_updates, posts_changes = build_updates(
        update_fields_posts, mapped_posts_data, extracted_posts_fields, previous_properties)
    followers_updates, followers_changes = build_updates(
        update_fields_followers, mapped_followers_data, extracted_followers_fields, current_properties)
    _add_next_relation(notion, database_id, parse_date(args.date), current_properties,
                       followers_updates, followers_changes)

    # Apply: update Notion pages
    update_ok = _apply_updates(
        notion, supabase_connection, args.yes,
        (previous_day_row.get('id', ''), posts_updates, posts_changes),
        (current_day_row.get('id', ''), followers_updates, followers_changes),
    )

    # Close Supabase connection
    supabase_connection.close()
    logger.info("=" * 60)

    logger.info("✅ Notion Update Process completed")
    return update_ok


if __name__ == "__main__":
    main()
