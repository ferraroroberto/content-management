#!/usr/bin/env python
"""
Initialization script to run the complete data processing pipeline:
1. social_api_client - Fetch data from social media APIs
2. data_processor - Process and transform the raw data
3. profile_aggregator - Aggregate profile data across platforms
4. posts_consolidator - Consolidate posts data across platforms
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Add the current directory to the Python path
sys.path.append(str(Path(__file__).parent))
from config.logger_config import setup_logger
from social_client.social_api_client import main as run_social_api_client, configure_logger as configure_social_logger
from process.data_processor import main as run_data_processor, configure_logger as configure_data_processor_logger
from process.profile_aggregator import main as run_profile_aggregator, configure_logger as configure_profile_logger
from process.posts_consolidator import main as run_posts_consolidator, configure_logger as configure_posts_logger

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
    return parser.parse_args()

def run_pipeline(debug_mode=False, skip_api=False, skip_processing=False, 
                skip_aggregation=False, skip_consolidation=False):
    """Run the complete data processing pipeline."""
    # Configure the main logger
    configure_logger(debug_mode)
    
    logger.info("🚀 Starting the complete data processing pipeline")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Step 1: Fetch data from social media APIs
    if not skip_api:
        logger.info("📡 Step 1: Running Social API Client")
        configure_social_logger(debug_mode)
        try:
            # Override stdin to simulate user input (non-debug mode, all platforms)
            sys.stdin = open(os.devnull) if debug_mode else None
            run_social_api_client()
            logger.info("✅ Social API Client completed successfully")
        except Exception as e:
            logger.error(f"❌ Error in Social API Client: {e}")
            if debug_mode:
                raise
        finally:
            # Restore stdin
            sys.stdin = sys.__stdin__
    else:
        logger.info("⏭️ Skipping Social API Client step")
    
    # Step 2: Process the raw data
    if not skip_processing:
        logger.info("🔄 Step 2: Running Data Processor")
        configure_data_processor_logger(debug_mode)
        try:
            # Override stdin to simulate user input (non-debug mode, upload to Supabase, CSV format)
            sys.stdin = open(os.devnull) if debug_mode else None
            run_data_processor()
            logger.info("✅ Data Processor completed successfully")
        except Exception as e:
            logger.error(f"❌ Error in Data Processor: {e}")
            if debug_mode:
                raise
        finally:
            # Restore stdin
            sys.stdin = sys.__stdin__
    else:
        logger.info("⏭️ Skipping Data Processor step")
    
    # Step 3: Aggregate profile data
    if not skip_aggregation:
        logger.info("📊 Step 3: Running Profile Aggregator")
        configure_profile_logger()
        try:
            run_profile_aggregator()
            logger.info("✅ Profile Aggregator completed successfully")
        except Exception as e:
            logger.error(f"❌ Error in Profile Aggregator: {e}")
            if debug_mode:
                raise
    else:
        logger.info("⏭️ Skipping Profile Aggregator step")
    
    # Step 4: Consolidate posts data
    if not skip_consolidation:
        logger.info("📑 Step 4: Running Posts Consolidator")
        configure_posts_logger(debug_mode)
        try:
            # Override stdin to simulate user input (non-debug mode)
            sys.stdin = open(os.devnull) if debug_mode else None
            run_posts_consolidator()
            logger.info("✅ Posts Consolidator completed successfully")
        except Exception as e:
            logger.error(f"❌ Error in Posts Consolidator: {e}")
            if debug_mode:
                raise
        finally:
            # Restore stdin
            sys.stdin = sys.__stdin__
    else:
        logger.info("⏭️ Skipping Posts Consolidator step")
    
    logger.info("🎉 Complete data processing pipeline finished")

def main():
    """Main function to run the complete pipeline."""
    args = parse_arguments()
    
    run_pipeline(
        debug_mode=args.debug,
        skip_api=args.skip_api,
        skip_processing=args.skip_processing,
        skip_aggregation=args.skip_aggregation,
        skip_consolidation=args.skip_consolidation
    )

if __name__ == "__main__":
    main()
