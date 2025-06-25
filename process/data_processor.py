import json
import pandas as pd
from datetime import datetime
import os
import logging
import sys
from pathlib import Path
import glob

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("data_processor", file_logging=False, level=log_level)
    return logger

def load_mapping_config():
    """Load mapping configuration from mapping.json file."""
    logger.debug("📂 Loading mapping configuration file")
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
    config_path = os.path.join(config_dir, 'mapping.json')
    
    try:
        with open(config_path, 'r') as file:
            logger.info("✅ Mapping configuration loaded successfully")
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"❌ Error: Mapping configuration file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        logger.error(f"❌ Error: Invalid JSON in mapping configuration file at {config_path}")
        return None

def get_nested_value(data, path):
    """Extract value from nested dictionary using dot notation path."""
    try:
        keys = path.split('.')
        value = data
        for key in keys:
            # Check if the key represents an array index
            if key.isdigit():
                # Convert to integer for array access
                key = int(key)
            
            # Handle both dictionary keys and list indices
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and isinstance(key, int) and key < len(value):
                value = value[key]
            else:
                # Key not found
                return None
                
        return value
    except (KeyError, TypeError, IndexError) as e:
        logger.debug(f"Error accessing path {path}: {str(e)}")
        return None

def extract_field_value(data, field_config):
    """Extract field value based on configuration."""
    path = field_config.get('path')
    field_type = field_config.get('type', 'string')
    
    if field_type == 'count':
        # Special handling for count type - count items in array
        value = get_nested_value(data, path)
        if isinstance(value, list):
            return len(value)
        else:
            return 0
    elif field_type == 'boolean_exists':
        # Check if a key exists (for detecting video posts)
        value = get_nested_value(data, path)
        return value is not None
    elif field_type == 'custom':
        # Handle custom transformations
        value = get_nested_value(data, path)
        transform_expr = field_config.get('transform')
        if transform_expr:
            try:
                # Execute the transformation expression
                # For security, limit to simple expressions
                result = eval(transform_expr, {"__builtins__": {}}, {"value": value})
                return result  # This should return a boolean
            except Exception as e:
                logger.error(f"❌ Error applying custom transformation: {e}")
                return None
        return value
    else:
        # Regular field extraction
        value = get_nested_value(data, path)
        
        # Type conversion
        if value is not None and field_type == 'integer':
            try:
                return int(value)
            except (ValueError, TypeError):
                return None
                
        return value

def extract_date_from_filename(file_path):
    """Extract date from filename like 'linkedin_posts_2025-06-25.json'."""
    filename = os.path.basename(file_path)
    # Try to find date pattern YYYY-MM-DD in filename
    import re
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if date_match:
        return date_match.group(1)
    return None

def convert_unix_timestamp(timestamp):
    """Convert Unix timestamp to human-readable date format."""
    if timestamp is None:
        return None
    
    # If it's already a formatted date string (contains dashes or colons), return as is
    if isinstance(timestamp, str) and ('-' in timestamp or ':' in timestamp):
        return timestamp
        
    try:
        # Try to convert as Unix timestamp (seconds since epoch)
        return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OSError) as e:
        # If conversion fails, return the original value
        logger.warning(f"⚠️ Failed to convert timestamp: {timestamp}. Error: {e}")
        return timestamp

def process_array_data(data, mapping_config, file_date=None):
    """Process array data type (like LinkedIn posts)."""
    array_path = mapping_config.get('array_path', 'data')
    array_data = get_nested_value(data, array_path)
    
    # Special handling for finding arrays within structures (e.g., Twitter)
    find_array_config = mapping_config.get('find_array')
    if find_array_config and isinstance(array_data, list):
        # Search for the correct item in the array
        target_type = find_array_config.get('type')
        entries_path = find_array_config.get('entries_path', 'entries')
        
        for item in array_data:
            if isinstance(item, dict) and item.get('type') == target_type:
                # Found the correct instruction, get the entries
                array_data = item.get(entries_path, [])
                break
        else:
            # If we didn't find the target type, array_data might still be the instructions list
            logger.warning(f"⚠️ Could not find instruction with type '{target_type}'")
            array_data = []
    
    if not isinstance(array_data, list):
        logger.warning(f"⚠️ Expected array at path {array_path}, got {type(array_data)}")
        return []
    
    field_mappings = mapping_config.get('fields', {})
    results = []
    
    for index, item in enumerate(array_data):
        # Skip non-tweet entries for Twitter (e.g., who-to-follow, cursors)
        if 'find_array' in mapping_config:  # This is Twitter
            entry_id = item.get('entryId', '')
            if not entry_id.startswith('tweet-'):
                logger.debug(f"Skipping non-tweet entry: {entry_id}")
                continue
        
        # Extract all mapped fields
        record = {}
        skip_record = False
        missing_fields = []
        
        for field_name, field_config in field_mappings.items():
            value = extract_field_value(item, field_config)
            
            if value is None and field_config.get('required', False):
                missing_fields.append(field_name)
                if logger.level <= logging.DEBUG:
                    # More detailed debug logging
                    path = field_config.get('path')
                    logger.debug(f"DEBUG: Missing required field '{field_name}' (path: {path}) in record {index+1}")
                    
                    # Check if the parent object exists
                    parent_path = '.'.join(path.split('.')[:-1]) if '.' in path else ''
                    if parent_path:
                        parent_obj = get_nested_value(item, parent_path)
                        if parent_obj is None:
                            logger.debug(f"DEBUG: Parent object at '{parent_path}' does not exist")
                        else:
                            logger.debug(f"DEBUG: Parent object exists: {parent_obj}")
                    
                    # Log a sample of the record for debugging
                    try:
                        import json
                        record_sample = json.dumps(item, indent=2)[:500]  # First 500 chars to avoid huge logs
                        logger.debug(f"DEBUG: Record sample: {record_sample}...")
                    except:
                        logger.debug(f"DEBUG: Unable to serialize record for logging")
                
                skip_record = True
                break
            
            # Convert Unix timestamps to readable dates
            if field_name == 'posted_at' and value is not None:
                # Store directly without keeping the raw value
                record[field_name] = convert_unix_timestamp(value)
            else:
                record[field_name] = value
        
        if skip_record:
            logger.warning(f"⚠️  Required field(s) {', '.join(missing_fields)} not found, skipping record {index+1}")
            continue
        
        # No date filtering here; process all records in the file

        results.append(record)
    
    return results

def process_json_file(file_path, mapping_config):
    """Process a single JSON file and extract mapped fields."""
    logger.debug(f"📄 Processing file: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
        # Extract metadata
        date = data.get('date')
        platform = data.get('platform', '').lower()  # Normalize to lowercase
        data_type = data.get('data_type', '').lower()  # Normalize to lowercase
        
        # Extract date from filename for filtering only (not to be included in final DataFrame)
        file_date = extract_date_from_filename(file_path)
        
        # Build platform key for mapping lookup
        platform_key = f"{platform}_{data_type}"
        
        if platform_key not in mapping_config:
            logger.warning(f"⚠️ No mapping configuration found for {platform_key}")
            return None
        
        platform_config = mapping_config[platform_key]
        
        # Check if this is array data type
        if platform_config.get('type') == 'array':
            records = process_array_data(data, platform_config, file_date)
            
            # Add metadata to each record (include date, but not file_date in the final records)
            for record in records:
                record['date'] = date
                record['platform'] = platform
                record['data_type'] = data_type
                # Add file_date as a temporary field for filtering only
                record['_file_date_temp'] = file_date
            
            logger.info(f"✅ Extracted {len(records)} records from {os.path.basename(file_path)}")
            return records
        else:
            # Process single record (existing logic)
            result = {
                'date': date,
                'platform': platform,
                'data_type': data_type,
                # Add file_date as a temporary field for filtering only
                '_file_date_temp': file_date
            }
            
            field_mappings = platform_config.get('fields', {})
            
            for field_name, field_config in field_mappings.items():
                value = extract_field_value(data, field_config)
                
                if value is None and field_config.get('required', False):
                    logger.warning(f"⚠️  Required field '{field_name}' not found in {file_path}")
                
                result[field_name] = value
            
            logger.debug(f"✅ Extracted data: {result}")
            return [result]  # Return as list for consistency
        
    except Exception as e:
        logger.error(f"❌ Error processing file {file_path}: {e}")
        return None

def get_json_files(results_dir):
    """Get all JSON files from the results directory."""
    json_pattern = os.path.join(results_dir, '*.json')
    files = glob.glob(json_pattern)
    logger.info(f"🔍 Found {len(files)} JSON files in results directory")
    return sorted(files)

def order_dataframe_columns(df, mapping_config):
    """Order DataFrame columns based on mapping configuration."""
    # Start with required columns in specific order (without file_date)
    ordered_columns = ['date', 'platform', 'data_type']
    
    # Get all field names from mapping config in order
    for platform_key, platform_config in mapping_config.items():
        fields = platform_config.get('fields', {})
        for field_name in fields.keys():
            if field_name not in ordered_columns and field_name in df.columns:
                ordered_columns.append(field_name)
    
    # Add any remaining columns not in mapping (except _file_date_temp)
    for col in df.columns:
        if col not in ordered_columns and col != '_file_date_temp':
            ordered_columns.append(col)
    
    # Reorder DataFrame columns
    return df[ordered_columns]

def process_all_files(mapping_config, results_dir=None, debug_mode=False):
    """Process all JSON files in the results directory and create separate DataFrames by data type."""
    if results_dir is None:
        results_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results')
    
    if not os.path.exists(results_dir):
        logger.error(f"❌ Results directory not found: {results_dir}")
        return {}
    
    # Get all JSON files
    json_files = get_json_files(results_dir)
    
    if not json_files:
        logger.warning("⚠️ No JSON files found in results directory")
        return {}
    
    # Get current date for filtering
    from datetime import datetime
    current_date = datetime.now().strftime('%Y-%m-%d')
    logger.info(f"📅 Filtering data for current date: {current_date}")
    
    # Organize data by platform and data type
    data_by_platform_type = {}
    processed = 0
    
    for file_path in json_files:
        logger.info(f"🚀 Processing file {processed+1}/{len(json_files)}: {os.path.basename(file_path)}")
        
        records = process_json_file(file_path, mapping_config)
        if records:
            # Group records by platform and data_type
            for record in records:
                platform = record.get('platform', '').lower()  # Normalize to lowercase
                data_type = record.get('data_type', '').lower()  # Normalize to lowercase
                key = f"{platform}_{data_type}"
                
                if key not in data_by_platform_type:
                    data_by_platform_type[key] = []
                data_by_platform_type[key].append(record)
        
        processed += 1
        logger.info(f"📊 Progress: {processed}/{len(json_files)} files processed ({(processed/len(json_files))*100:.1f}%)")
    
    # Create separate DataFrames for each platform and data type
    dataframes = {}
    for key, records in data_by_platform_type.items():
        # Create DataFrame for this platform and data type
        df = pd.DataFrame(records)
        
        if df.empty:
            logger.warning(f"⚠️ No data extracted for: {key}")
            continue
        
        # Filter by current date using temporary _file_date_temp field
        initial_count = len(df)
        df = df[df['_file_date_temp'] == current_date]
        filtered_count = len(df)
        logger.info(f"🔍 Filtered {key} from {initial_count} to {filtered_count} records for date {current_date}")
        
        # Remove temporary file_date field
        df = df.drop(columns=['_file_date_temp'])
        
        # Skip if no data after filtering
        if filtered_count == 0:
            logger.warning(f"⚠️ No data for {key} after date filtering")
            continue
        
        # Order columns based on mapping configuration
        df = order_dataframe_columns(df, mapping_config)
        
        # Sort by posted date if available
        if 'posted' in df.columns:
            df = df.sort_values(['posted'], ascending=False)
        
        logger.info(f"✅ Created DataFrame for {key} with {len(df)} rows and {len(df.columns)} columns")
        dataframes[key] = df
    
    return dataframes

def save_dataframes(dataframes, output_format='csv', output_dir=None):
    """Save multiple DataFrames to files in specified format."""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results')

    saved_files = []
    
    for key, df in dataframes.items():
        if output_format.lower() == 'excel':
            filename = f"{key}.xlsx"
            file_path = os.path.join(output_dir, filename)
            df.to_excel(file_path, index=False)
        else:  # CSV
            filename = f"{key}.csv"
            file_path = os.path.join(output_dir, filename)
            df.to_csv(file_path, index=False)
        
        logger.info(f"💾 Saved {key} DataFrame to {output_format.upper()}: {file_path}")
        saved_files.append(file_path)
    
    return saved_files

def main():
    """Main function to execute the data processor."""
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode? (y/n): ").lower()
    debug_mode = debug_input == 'y'
    
    # Configure logger with appropriate level
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Data Processor")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load mapping configuration
    mapping_config = load_mapping_config()
    if not mapping_config:
        logger.error("❌ Failed to load mapping configuration")
        return
    
    # Process all JSON files and get DataFrames by data type
    dataframes = process_all_files(mapping_config, debug_mode=debug_mode)
    
    if not dataframes:
        logger.warning("⚠️ No data to save")
        return
    
    # Display info about each DataFrame
    for data_type, df in dataframes.items():
        logger.info(f"📊 {data_type} DataFrame shape: {df.shape}")
        logger.info(f"📋 {data_type} Columns: {', '.join(df.columns)}")
        
        if debug_mode:
            print(f"\n📊 {data_type} DataFrame Preview:")
            print(df.head())
            print(f"\n📈 {data_type} DataFrame Info:")
            print(df.info())
    
    # Ask for output format
    format_input = input("\nSave as Excel or CSV? (excel/csv) [default: csv]: ").lower()
    output_format = 'excel' if format_input == 'excel' else 'csv'
    
    # Save DataFrames
    output_files = save_dataframes(dataframes, output_format=output_format)
    
    logger.info("✅ Data Processor completed")
    logger.info(f"📁 Generated {len(output_files)} output files")

if __name__ == "__main__":
    main()