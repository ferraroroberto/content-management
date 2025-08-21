#!/usr/bin/env python
"""
🚀 Social Media Automation Suite - Main Launcher

This is the main entry point for the Social Media Automation Suite.
It provides a clean interface to run the complete pipeline or individual components.

Usage:
    python3 launch.py                    # Run complete pipeline
    python3 launch.py --help            # Show help
    python3 launch.py --pipeline-only   # Run only the pipeline
    python3 launch.py --api-only        # Run only API collection
    python3 launch.py --process-only    # Run only data processing
    python3 launch.py --notion-only     # Run only Notion sync
"""

import sys
import argparse
from pathlib import Path

# Add the current directory to the Python path
sys.path.append(str(Path(__file__).parent))

from process.pipeline import run_pipeline, configure_logger

def parse_arguments():
    """Parse command line arguments for the main launcher."""
    parser = argparse.ArgumentParser(
        description='🚀 Social Media Automation Suite - Main Launcher',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 launch.py                    # Run complete pipeline
    python3 launch.py --pipeline-only   # Run only the pipeline
    python3 launch.py --api-only        # Run only API collection
    python3 launch.py --process-only    # Run only data processing
    python3 launch.py --notion-only     # Run only Notion sync
    python3 launch.py --debug           # Enable debug mode
        """
    )
    
    # Main execution modes
    parser.add_argument('--pipeline-only', action='store_true', 
                       help='Run only the complete pipeline (default)')
    parser.add_argument('--api-only', action='store_true', 
                       help='Run only the social media API collection')
    parser.add_argument('--process-only', action='store_true', 
                       help='Run only the data processing steps')
    parser.add_argument('--notion-only', action='store_true', 
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
    
    return parser.parse_args()

def main():
    """Main function to launch the appropriate automation component."""
    args = parse_arguments()
    
    print("🚀 Social Media Automation Suite")
    print("=" * 50)
    
    # Determine execution mode
    if args.api_only:
        print("📡 Running Social Media API Collection Only...")
        # Import and run only the API client
        from social_client.social_api_client import main as run_api_client
        run_api_client()
        
    elif args.process_only:
        print("🔄 Running Data Processing Only...")
        # Import and run only the data processor
        from process.data_processor import main as run_processor
        run_processor()
        
    elif args.notion_only:
        print("📘 Running Notion Synchronization Only...")
        # Import and run only the Notion update
        from notion.notion_update import main as run_notion
        run_notion()
        
    else:
        # Default: Run complete pipeline
        print("🚀 Running Complete Data Processing Pipeline...")
        run_pipeline(
            debug_mode=args.debug,
            skip_api=args.skip_api,
            skip_processing=args.skip_processing,
            skip_aggregation=args.skip_aggregation,
            skip_consolidation=args.skip_consolidation,
            skip_notion=args.skip_notion,
            reference_date=args.date
        )
    
    print("\n✅ Execution completed!")

if __name__ == "__main__":
    main()