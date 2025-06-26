import requests
import json
from datetime import datetime
import os
import logging
import sys
import time
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

def get_api_data(platform_key, config):
    """Fetch data using the API for the specified platform."""
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
    
    try:
        logger.debug(f"📡 Making API request to {url}")
        logger.debug(f"🔑 Using query parameters: {querystring}")
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()  # Raise an exception for bad status codes
        logger.info(f"✅ Successfully retrieved {platform_key} data")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error fetching {platform_key} data: {e}")
        return None

def save_results(platform_key, data, config):
    """Save the results to a JSON file in the results directory."""
    if not data:
        logger.warning(f"⚠️ No data to save for {platform_key}")
        return
    
    logger.info(f"💾 Saving {platform_key} data")
    
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

def process_all_endpoints(config, debug_mode=False, specific_platform=None):
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
    
    for platform_key in config_endpoints.keys():
        logger.info(f"🚀 Processing {platform_key} ({completed+1}/{total_endpoints})")
        
        # Get data
        data = get_api_data(platform_key, config)
        if data:
            # Save results
            file_path = save_results(platform_key, data, config)
            logger.info(f"✅ {platform_key} data saved to {file_path}")
        else:
            logger.error(f"❌ Failed to retrieve {platform_key} data")
        
        completed += 1
        logger.info(f"📊 Progress: {completed}/{total_endpoints} completed ({(completed/total_endpoints)*100:.1f}%)")
        
        # If in debug mode and not the last endpoint, ask user if they want to continue
        if debug_mode and completed < total_endpoints:
            proceed = input(f"\nContinue to next endpoint? (y/n): ").lower()
            if proceed != 'y':
                logger.info("⏹️  Processing stopped by user")
                break
            # Add a small delay to prevent rate limiting
            time.sleep(1)
    
    logger.info(f"✅ Completed processing {completed}/{total_endpoints} endpoints")

def main():
    """Main function to execute the social API client."""
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode? (y/n): ").lower()
    debug_mode = debug_input == 'y'
    
    # Configure logger with appropriate level
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting Social API Client")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Load configuration
    config = load_config()
    if not config:
        logger.error("❌ Failed to load configuration")
        return
    
    # Ask if user wants to process a specific platform
    specific_platform = input("Process specific platform? (press Enter for all, or type platform name): ").strip()
    
    # Process endpoints
    process_all_endpoints(config, debug_mode, specific_platform if specific_platform else None)
    
    logger.info("✅ Social API Client completed")

if __name__ == "__main__":
    main()
