#!/usr/bin/env python
"""
Initialization script to run the complete data processing pipeline:
1. social_api_client - Fetch data from social media APIs
2. data_processor - Process and transform the raw data
3. profile_aggregator - Aggregate profile data across platforms
4. posts_consolidator - Consolidate posts data across platforms
5. notion_update - Update Notion databases with processed data
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Add the current directory to the Python path
sys.path.append(str(Path(__file__).parent))
from config.logger_config import setup_logger
from social_client.social_api_client import main as run_social_api_client, configure_logger as configure_social_logger
from process.data_processor import main as run_data_processor, configure_logger as configure_data_processor_logger
from process.profile_aggregator import main as run_profile_aggregator, configure_logger as configure_profile_logger
from process.posts_consolidator import main as run_posts_consolidator, configure_logger as configure_posts_logger
from notion.notion_update import main as run_notion_update, configure_logger as configure_notion_logger

# Set up logger
logger = None

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("init", file_logging=False, level=log_level)
    return logger

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Run the complete data processing pipeline.')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('-s', '--skip-api', action='store_true', help='Skip the API data collection step')
    parser.add_argument('-p', '--skip-processing', action='store_true', help='Skip the data processing step')
    parser.add_argument('-a', '--skip-aggregation', action='store_true', help='Skip the profile aggregation step')
    parser.add_argument('-c', '--skip-consolidation', action='store_true', help='Skip the posts consolidation step')
    parser.add_argument('-n', '--skip-notion', action='store_true', help='Skip the Notion update step')
    parser.add_argument('--date', type=str, help='Reference date in YYYYMMDD format. Will process the day before this date.')
    return parser.parse_args()

def run_module(module_func, module_name, debug_mode=False, extra_args=None):
    """
    Run a module with clean command line arguments.
    
    Args:
        module_func: The module's main function to run
        module_name: Name of the module for logging
        debug_mode: Whether to enable debug mode
        extra_args: List of additional arguments to pass
    """
    # Save original command line arguments
    original_argv = sys.argv.copy()
    
    try:
        # Reset sys.argv to just the script name
        sys.argv = [original_argv[0]]
        
        # Add debug flag if needed
        if debug_mode:
            sys.argv.append('--debug')
            
        # Add any extra arguments
        if extra_args:
            sys.argv.extend(extra_args)
            
        # Run the module
        module_func()
        logger.info(f"✅ {module_name} completed successfully")
    except Exception as e:
        logger.error(f"❌ Error in {module_name}: {e}")
        if debug_mode:
            raise
    finally:
        # Restore original arguments
        sys.argv = original_argv.copy()

def run_pipeline(debug_mode=False, skip_api=False, skip_processing=False, 
                skip_aggregation=False, skip_consolidation=False, skip_notion=False,
                reference_date=None):
    """Run the complete data processing pipeline."""
    # Configure the main logger
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting the complete data processing pipeline")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Use the reference date directly or today's date
    if reference_date:
        try:
            processing_date = reference_date
            logger.info(f"📅 Using specified date: {processing_date}")
        except ValueError:
            logger.error(f"❌ Invalid date format: {reference_date}. Using current date.")
            processing_date = datetime.now().strftime("%Y%m%d")
    else:
        processing_date = datetime.now().strftime("%Y%m%d")
        logger.info(f"📅 No date specified. Using current date: {processing_date}")
    
    # Step 1: Fetch data from social media APIs
    if not skip_api:
        logger.info("📡 Step 1: Running Social API Client")
        configure_social_logger(debug_mode)
        run_module(run_social_api_client, "Social API Client", debug_mode)
    else:
        logger.info("⏭️ Skipping Social API Client step")
    
    # Step 2: Process the raw data
    if not skip_processing:
        logger.info("🔄 Step 2: Running Data Processor")
        configure_data_processor_logger(debug_mode)
        run_module(run_data_processor, "Data Processor", debug_mode)
    else:
        logger.info("⏭️ Skipping Data Processor step")
    
    # Step 3: Aggregate profile data
    if not skip_aggregation:
        logger.info("📊 Step 3: Running Profile Aggregator")
        configure_profile_logger()
        run_module(run_profile_aggregator, "Profile Aggregator", debug_mode)
    else:
        logger.info("⏭️ Skipping Profile Aggregator step")
    
    # Step 4: Consolidate posts data
    if not skip_consolidation:
        logger.info("📑 Step 4: Running Posts Consolidator")
        configure_posts_logger(debug_mode)
        run_module(run_posts_consolidator, "Posts Consolidator", debug_mode)
    else:
        logger.info("⏭️ Skipping Posts Consolidator step")
        
    # Step 5: Update Notion with processed data
    if not skip_notion:
        logger.info("📘 Step 5: Running Notion Update")
        configure_notion_logger(debug_mode)
        try:
            logger.info(f"🗓️  Using date for Notion update: {processing_date}")
            run_module(run_notion_update, "Notion Update", debug_mode, [processing_date])
        except Exception as e:
            logger.error(f"❌ Error in Notion Update: {e}")
            if debug_mode:
                raise
    else:
        logger.info("⏭️ Skipping Notion Update step")
    
    logger.info("🎉 Complete data processing pipeline finished")

def main():
    """Main function to run the complete pipeline."""
    args = parse_arguments()
    
    run_pipeline(
        debug_mode=args.debug,
        skip_api=args.skip_api,
        skip_processing=args.skip_processing,
        skip_aggregation=args.skip_aggregation,
        skip_consolidation=args.skip_consolidation,
        skip_notion=args.skip_notion,
        reference_date=args.date
    )

if __name__ == "__main__":
    main()
