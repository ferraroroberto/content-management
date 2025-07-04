import requests
import json
from datetime import datetime
import os
import logging
import sys
import time
import argparse
from pathlib import Path

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))
from config.logger_config import setup_logger

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("social_api_client", file_logging=False, level=log_level)
    return logger

def load_config():
    """Load configuration from config.json file."""
    logger.debug("📂 Loading configuration file")
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
    config_path = os.path.join(config_dir, 'config.json')
    
    try:
        with open(config_path, 'r') as file:
            logger.info("✅ Configuration loaded successfully")
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"❌ Error: Configuration file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        logger.error(f"❌ Error: Invalid JSON in configuration file at {config_path}")
        return None

def check_file_exists_for_today(platform_key, config):
    """Check if a file already exists for the current day for this platform."""
    # Determine platform and data type from platform_key
    parts = platform_key.split('_')
    platform = parts[0]
    data_type = parts[1] if len(parts) > 1 else "data"
    
    # Get results directory from config or use default
    results_dir_relative = config.get("folder_results_raw", "results/raw")
    
    # Create full path for results directory
    project_root = os.path.dirname(os.path.dirname(__file__))
    results_dir = os.path.join(project_root, *results_dir_relative.split('/'))
    
    # Generate today's filename
    current_date = datetime.now().strftime('%Y-%m-%d')
    filename = f"{platform}_{data_type}_{current_date}.json"
    file_path = os.path.join(results_dir, filename)
    
    # Check if file exists
    exists = os.path.exists(file_path)
    if exists:
        logger.info(f"🔄 Found existing file for {platform_key} dated today: {filename}")
    return exists, file_path

def get_api_data(platform_key, config):
    """Fetch data using the API for the specified platform with retries and exponential backoff."""
    if not config or platform_key not in config:
        logger.error(f"❌ {platform_key} configuration not found in config file")
        return None

    logger.info(f"🔍 Fetching {platform_key} data")
    api_config = config[platform_key]

    url = api_config.get('api_url')
    headers = {
        "x-rapidapi-key": api_config.get('api_key'),
        "x-rapidapi-host": api_config.get('api_host')
    }
    querystring = api_config.get('querystring', {})

    retries = 5
    backoff_times = [30, 60, 120, 240, 480]  # seconds

    for attempt in range(retries):
        try:
            logger.debug(f"📡 Making API request to {url} (attempt {attempt+1}/{retries})")
            logger.debug(f"🔑 Using query parameters: {querystring}")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()  # Raise an exception for bad status codes
            logger.info(f"✅ Successfully retrieved {platform_key} data")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error fetching {platform_key} data (attempt {attempt+1}/{retries}): {e}")
            # Only backoff if not the last attempt
            if attempt < retries - 1:
                wait_time = backoff_times[attempt]
                logger.info(f"⏳ Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ All {retries} attempts failed for {platform_key}")
                return None

def save_results(platform_key, data, config):
    """Save the results to a JSON file in the results directory."""
    if not data:
        logger.warning(f"⚠️ No data to save for {platform_key}")
        return
    
    logger.info(f"💾 Saving {platform_key} data")
    
    # Check if a file for today already exists
    exists, file_path = check_file_exists_for_today(platform_key, config)
    if exists:
        logger.info(f"📂 Skipping save: File for {platform_key} already exists for today")
        return file_path  # Return the existing file path
    
    # Determine platform and data type from platform_key
    parts = platform_key.split('_')
    platform = parts[0]
    data_type = parts[1] if len(parts) > 1 else "data"
    
    # Get results directory from config or use default
    results_dir_relative = config.get("folder_results_raw", "results/raw")
    
    # Create full path for results directory
    project_root = os.path.dirname(os.path.dirname(__file__))
    results_dir = os.path.join(project_root, *results_dir_relative.split('/'))
    
    # Create results directory if it doesn't exist
    os.makedirs(results_dir, exist_ok=True)
    
    # Add metadata
    current_date = datetime.now().strftime('%Y-%m-%d')
    result_data = {
        "date": current_date,
        "platform": platform,
        "data_type": data_type,
        "data": data
    }
    
    # Generate filename with date
    filename = f"{platform}_{data_type}_{current_date}.json"
    file_path = os.path.join(results_dir, filename)
    
    # Check if file already exists and delete it
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"🗑️  Deleted existing file: {file_path}")
    
    # Save to file
    with open(file_path, 'w', encoding='utf-8') as file:
        json.dump(result_data, file, indent=4, ensure_ascii=False)
    
    logger.info(f"✅ Results saved to {file_path}")
    return file_path

def process_all_endpoints(config, debug_mode=False, specific_platform=None, skip_existing=True):
    """Process all API endpoints defined in the config file or a specific one if provided."""
    if not config:
        logger.error("❌ Failed to load configuration")
        return
    
    # Filter out configuration parameters that aren't API endpoints
    config_endpoints = {k: v for k, v in config.items() if isinstance(v, dict) and 'api_url' in v}
    
    # Filter for specific platform if provided
    if specific_platform:
        if specific_platform in config_endpoints:
            config_endpoints = {specific_platform: config_endpoints[specific_platform]}
            logger.info(f"🎯 Processing only '{specific_platform}' endpoint")
        else:
            logger.error(f"❌ Specified platform '{specific_platform}' not found in configuration")
            logger.info(f"📋 Available platforms: {', '.join(config_endpoints.keys())}")
            return
    
    # Count total number of endpoints
    total_endpoints = len(config_endpoints.keys())
    logger.info(f"🔢 Found {total_endpoints} endpoints to process")
    
    # Process each endpoint
    completed = 0
    skipped = 0
    
    for platform_key in config_endpoints.keys():
        logger.info(f"🚀 Processing {platform_key} ({completed+1+skipped}/{total_endpoints})")
        
        # Check if file already exists for today
        if skip_existing:
            file_exists, file_path = check_file_exists_for_today(platform_key, config)
            if file_exists:
                logger.info(f"⏩ Skipping {platform_key} - data already collected today")
                skipped += 1
                logger.info(f"📊 Progress: {completed+skipped}/{total_endpoints} processed ({(completed+skipped)/total_endpoints*100:.1f}%) [{skipped} skipped]")
                continue
        
        # Get data
        data = get_api_data(platform_key, config)
        if data:
            # Save results
            file_path = save_results(platform_key, data, config)
            logger.info(f"✅ {platform_key} data saved to {file_path}")
        else:
            logger.error(f"❌ Failed to retrieve {platform_key} data")
        
        completed += 1
        logger.info(f"📊 Progress: {completed+skipped}/{total_endpoints} processed ({(completed+skipped)/total_endpoints*100:.1f}%) [{skipped} skipped]")
        
        # If in debug mode and not the last endpoint, ask user if they want to continue
        if debug_mode and (completed + skipped) < total_endpoints:
            proceed = input(f"\nContinue to next endpoint? (y/n): ").lower()
            if proceed != 'y':
                logger.info("⏹️  Processing stopped by user")
                break
            # Add a small delay to prevent rate limiting
            time.sleep(1)
    
    logger.info(f"✅ Completed processing {completed+skipped}/{total_endpoints} endpoints")
    if skipped > 0:
        logger.info(f"🔄 {skipped} endpoints were skipped - data already collected today")
    logger.info(f"📈 {completed} new data collections performed")

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Social API Client to fetch data from various platforms.')
    
    # Add arguments for all interactive prompts
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--skip-existing', action='store_true', default=True, 
                        help='Skip platforms with today\'s data already collected')
    parser.add_argument('--no-skip', action='store_false', dest='skip_existing',
                        help='Do not skip platforms with today\'s data already collected')
    parser.add_argument('--platform', type=str, default=None,
                        help='Process specific platform (leave empty for all platforms)')
    
    return parser.parse_args()

def main(args=None):
    """Main function to execute the social API client."""
    if args is None:
        # Use command-line arguments if available, otherwise parse them
        args = parse_arguments()
    
    # Configure logger with appropriate level based on args
    debug_mode = args.debug
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Social API Client")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load configuration
    config = load_config()
    if not config:
        logger.error("❌ Failed to load configuration")
        return
    
    # Use arguments instead of prompts
    skip_existing = args.skip_existing
    logger.info(f"⏩ Skip existing: {'Enabled' if skip_existing else 'Disabled'}")
    
    specific_platform = args.platform
    if specific_platform:
        logger.info(f"🎯 Targeting specific platform: {specific_platform}")
    
    # Process endpoints
    process_all_endpoints(config, debug_mode, specific_platform, skip_existing)
    
    logger.info("✅ Social API Client completed")

if __name__ == "__main__":
    main()
