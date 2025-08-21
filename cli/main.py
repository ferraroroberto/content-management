#!/usr/bin/env python
"""
🖥️ Social Media Automation Suite - CLI Interface

This module provides a command-line interface for the Social Media Automation Suite.
It offers an alternative to the main launcher with more detailed CLI options and
better integration with shell scripts and automation tools.

Usage:
    python3 -m cli.main                    # Run complete pipeline
    python3 -m cli.main --help            # Show help
    python3 -m cli.main --pipeline-only   # Run only the pipeline
    python3 -m cli.main --api-only        # Run only API collection
    python3 -m cli.main --process-only    # Run only data processing
    python3 -m cli.main --notion-only     # Run only Notion sync
"""

import sys
import argparse
import logging
from pathlib import Path

# Add the parent directory to the Python path
sys.path.append(str(Path(__file__).parent.parent))

from process.pipeline import run_pipeline
from config.logger_config import setup_logger

def setup_cli_logger(debug_mode=False):
    """Set up CLI-specific logger."""
    log_level = logging.DEBUG if debug_mode else logging.INFO
    return setup_logger("cli", file_logging=False, level=log_level)

def parse_cli_arguments():
    """Parse command line arguments for the CLI interface."""
    parser = argparse.ArgumentParser(
        prog='social-automation',
        description='🖥️ Social Media Automation Suite - CLI Interface',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 -m cli.main                    # Run complete pipeline
    python3 -m cli.main --pipeline-only   # Run only the pipeline
    python3 -m cli.main --api-only        # Run only API collection
    python3 -m cli.main --process-only    # Run only data processing
    python3 -m cli.main --notion-only     # Run only Notion sync
    python3 -m cli.main --debug           # Enable debug mode
    
Environment Variables:
    DEBUG                   Set to '1' to enable debug mode
    SKIP_API               Set to '1' to skip API collection
    SKIP_PROCESSING        Set to '1' to skip data processing
    SKIP_AGGREGATION       Set to '1' to skip profile aggregation
    SKIP_CONSOLIDATION     Set to '1' to skip posts consolidation
    SKIP_NOTION            Set to '1' to skip Notion sync
    REFERENCE_DATE         Set to YYYYMMDD format for processing date
        """
    )
    
    # Main execution modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--pipeline-only', action='store_true', 
                           help='Run only the complete pipeline (default)')
    mode_group.add_argument('--api-only', action='store_true', 
                           help='Run only the social media API collection')
    mode_group.add_argument('--process-only', action='store_true', 
                           help='Run only the data processing steps')
    mode_group.add_argument('--notion-only', action='store_true', 
                           help='Run only the Notion synchronization')
    
    # Pipeline control options
    parser.add_argument('-d', '--debug', action='store_true', 
                       help='Enable debug mode')
    parser.add_argument('-s', '--skip-api', action='store_true', 
                       help='Skip the API data collection step')
    parser.add_argument('-p', '--skip-processing', action='store_true', 
                       help='Skip the data processing step')
    parser.add_argument('-a', '--skip-aggregation', action='store_true', 
                       help='Skip the profile aggregation step')
    parser.add_argument('-c', '--skip-consolidation', action='store_true', 
                       help='Skip the posts consolidation step')
    parser.add_argument('-n', '--skip-notion', action='store_true', 
                       help='Skip the Notion update step')
    parser.add_argument('--date', type=str, 
                       help='Reference date in YYYYMMDD format. Will process the day before this date.')
    
    # Output and logging options
    parser.add_argument('-v', '--verbose', action='store_true', 
                       help='Enable verbose output')
    parser.add_argument('--quiet', action='store_true', 
                       help='Suppress all output except errors')
    parser.add_argument('--log-file', type=str, 
                       help='Log to specified file instead of console')
    
    return parser.parse_args()

def get_environment_overrides():
    """Get configuration overrides from environment variables."""
    import os
    
    overrides = {}
    
    # Check for environment variable overrides
    if os.getenv('DEBUG') == '1':
        overrides['debug'] = True
    if os.getenv('SKIP_API') == '1':
        overrides['skip_api'] = True
    if os.getenv('SKIP_PROCESSING') == '1':
        overrides['skip_processing'] = True
    if os.getenv('SKIP_AGGREGATION') == '1':
        overrides['skip_aggregation'] = True
    if os.getenv('SKIP_CONSOLIDATION') == '1':
        overrides['skip_consolidation'] = True
    if os.getenv('SKIP_NOTION') == '1':
        overrides['skip_notion'] = True
    
    reference_date = os.getenv('REFERENCE_DATE')
    if reference_date:
        overrides['reference_date'] = reference_date
    
    return overrides

def main():
    """Main CLI function."""
    args = parse_cli_arguments()
    
    # Get environment overrides
    env_overrides = get_environment_overrides()
    
    # Merge command line arguments with environment overrides
    # Command line takes precedence
    debug_mode = args.debug or env_overrides.get('debug', False)
    skip_api = args.skip_api or env_overrides.get('skip_api', False)
    skip_processing = args.skip_processing or env_overrides.get('skip_processing', False)
    skip_aggregation = args.skip_aggregation or env_overrides.get('skip_aggregation', False)
    skip_consolidation = args.skip_consolidation or env_overrides.get('skip_consolidation', False)
    skip_notion = args.skip_notion or env_overrides.get('skip_notion', False)
    reference_date = args.date or env_overrides.get('reference_date')
    
    # Set up logging
    logger = setup_cli_logger(debug_mode)
    
    if not args.quiet:
        print("🖥️ Social Media Automation Suite - CLI Interface")
        print("=" * 60)
    
    # Determine execution mode
    if args.api_only:
        if not args.quiet:
            print("📡 Running Social Media API Collection Only...")
        logger.info("Starting API-only execution")
        
        # Import and run only the API client
        from social_client.social_api_client import main as run_api_client
        run_api_client()
        
    elif args.process_only:
        if not args.quiet:
            print("🔄 Running Data Processing Only...")
        logger.info("Starting process-only execution")
        
        # Import and run only the data processor
        from process.data_processor import main as run_processor
        run_processor()
        
    elif args.notion_only:
        if not args.quiet:
            print("📘 Running Notion Synchronization Only...")
        logger.info("Starting Notion-only execution")
        
        # Import and run only the Notion update
        from notion.notion_update import main as run_notion
        run_notion()
        
    else:
        # Default: Run complete pipeline
        if not args.quiet:
            print("🚀 Running Complete Data Processing Pipeline...")
        logger.info("Starting complete pipeline execution")
        
        run_pipeline(
            debug_mode=debug_mode,
            skip_api=skip_api,
            skip_processing=skip_processing,
            skip_aggregation=skip_aggregation,
            skip_consolidation=skip_consolidation,
            skip_notion=skip_notion,
            reference_date=reference_date
        )
    
    if not args.quiet:
        print("\n✅ CLI execution completed!")
    
    logger.info("CLI execution completed successfully")

if __name__ == "__main__":
    main()