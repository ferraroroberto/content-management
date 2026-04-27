#!/usr/bin/env python3
"""
example_usage.py

Example usage of the new simplified Notion relations system.
Shows different ways to use the Python integration.
"""

import logging
import sys
from pathlib import Path

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))

from notion.notion_relations_python_setup import NotionRelationsPythonSetup

log = logging.getLogger(__name__)


def example_basic_setup():
    """Example 1: Basic setup and usage."""
    log.info("Example 1: Basic Setup and Usage")

    setup = NotionRelationsPythonSetup(environment="cloud")

    log.info("Setting up the system...")
    if setup.setup_complete_system():
        log.info("System setup successful!")

        log.info("Testing the system...")
        if setup.run_quick_test():
            log.info("System is working correctly!")
        else:
            log.error("System test failed")
    else:
        log.error("System setup failed")


def example_verification_only():
    """Example 2: Just verify the system is working."""
    log.info("Example 2: Verification Only")

    setup = NotionRelationsPythonSetup(environment="cloud")

    setup.connection = setup._get_database_connection()
    if not setup.connection:
        log.error("Could not connect to database")
        return

    try:
        log.info("Verifying system components...")
        if setup.verify_setup():
            log.info("All components verified successfully!")
        else:
            log.error("Some components failed verification")
    finally:
        setup.connection.close()


def example_demo_queries():
    """Example 3: Run demo queries to see the system in action."""
    log.info("Example 3: Demo Queries")

    setup = NotionRelationsPythonSetup(environment="cloud")

    setup.connection = setup._get_database_connection()
    if not setup.connection:
        log.error("Could not connect to database")
        return

    try:
        log.info("Running demo queries...")
        if setup.run_demo_queries():
            log.info("Demo queries completed successfully!")
        else:
            log.error("Demo queries failed")
    finally:
        setup.connection.close()


def example_custom_config():
    """Example 4: Using a custom configuration file."""
    log.info("Example 4: Custom Configuration")

    custom_config = "config/custom_config.json"
    setup = NotionRelationsPythonSetup(
        config_path=custom_config,
        environment="local"
    )

    log.info("Using custom config: %s  Environment: local", custom_config)

    if setup.run_quick_test():
        log.info("Custom configuration working!")
    else:
        log.error("Custom configuration failed")


def example_incremental_setup():
    """Example 5: Incremental setup steps."""
    log.info("Example 5: Incremental Setup")

    setup = NotionRelationsPythonSetup(environment="cloud")

    setup.connection = setup._get_database_connection()
    if not setup.connection:
        log.error("Could not connect to database")
        return

    try:
        log.info("Step 1: Setting up core system...")
        if setup.setup_core_system():
            log.info("Core system setup successful!")
        else:
            log.error("Core system setup failed")
            return

        log.info("Step 2: Setting up automatic relations...")
        if setup.setup_automatic_relations():
            log.info("Automatic relations setup successful!")
        else:
            log.error("Automatic relations setup failed")
            return

        log.info("Step 3: Verifying setup...")
        if setup.verify_setup():
            log.info("All verification checks passed!")
        else:
            log.error("Some verification checks failed")

        log.info("Step 4: Running demo queries...")
        if setup.run_demo_queries():
            log.info("Demo queries successful!")
        else:
            log.warning("Demo queries failed, but setup may still be successful")

    finally:
        setup.connection.close()


def example_error_handling():
    """Example 6: Error handling and recovery."""
    log.info("Example 6: Error Handling")

    try:
        setup = NotionRelationsPythonSetup(
            config_path="nonexistent_config.json",
            environment="cloud"
        )

        log.info("This should fail gracefully...")
        setup.setup_complete_system()

    except FileNotFoundError as e:
        log.info("Error handled correctly: %s", e)
    except Exception as e:
        log.error("Unexpected error: %s", e)


def main():
    """Run all examples."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("Notion Relations Python Integration Examples")

    example_basic_setup()
    example_verification_only()
    example_demo_queries()
    example_custom_config()
    example_incremental_setup()
    example_error_handling()

    log.info("All examples completed!")
    log.info("To use the system in your own code:")
    log.info("  from notion.notion_relations_python_setup import NotionRelationsPythonSetup")
    log.info("  setup = NotionRelationsPythonSetup(environment='cloud')")
    log.info("  setup.setup_complete_system()")

if __name__ == "__main__":
    main()
