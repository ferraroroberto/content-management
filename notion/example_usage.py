#!/usr/bin/env python3
"""
example_usage.py

Example usage of the new simplified Notion relations system.
Shows different ways to use the Python integration.
"""

import sys
from pathlib import Path

# Add the parent directory to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent))

from notion.notion_relations_python_setup import NotionRelationsPythonSetup

def example_basic_setup():
    """Example 1: Basic setup and usage."""
    print("🚀 Example 1: Basic Setup and Usage")
    print("=" * 50)
    
    # Initialize the setup system
    setup = NotionRelationsPythonSetup(environment="cloud")
    
    # Setup the complete system
    print("📦 Setting up the system...")
    if setup.setup_complete_system():
        print("✅ System setup successful!")
        
        # Run a quick test
        print("🧪 Testing the system...")
        if setup.run_quick_test():
            print("✅ System is working correctly!")
        else:
            print("❌ System test failed")
    else:
        print("❌ System setup failed")
    
    print()

def example_verification_only():
    """Example 2: Just verify the system is working."""
    print("🔍 Example 2: Verification Only")
    print("=" * 50)
    
    # Initialize the setup system
    setup = NotionRelationsPythonSetup(environment="cloud")
    
    # Get database connection
    setup.connection = setup._get_database_connection()
    if not setup.connection:
        print("❌ Could not connect to database")
        return
    
    try:
        # Verify the setup
        print("🔍 Verifying system components...")
        if setup.verify_setup():
            print("✅ All components verified successfully!")
        else:
            print("❌ Some components failed verification")
    finally:
        setup.connection.close()
    
    print()

def example_demo_queries():
    """Example 3: Run demo queries to see the system in action."""
    print("🎯 Example 3: Demo Queries")
    print("=" * 50)
    
    # Initialize the setup system
    setup = NotionRelationsPythonSetup(environment="cloud")
    
    # Get database connection
    setup.connection = setup._get_database_connection()
    if not setup.connection:
        print("❌ Could not connect to database")
        return
    
    try:
        # Run demo queries
        print("🎯 Running demo queries...")
        if setup.run_demo_queries():
            print("✅ Demo queries completed successfully!")
        else:
            print("❌ Demo queries failed")
    finally:
        setup.connection.close()
    
    print()

def example_custom_config():
    """Example 4: Using a custom configuration file."""
    print("⚙️ Example 4: Custom Configuration")
    print("=" * 50)
    
    # Initialize with custom config
    custom_config = "config/custom_config.json"
    setup = NotionRelationsPythonSetup(
        config_path=custom_config,
        environment="local"
    )
    
    print(f"📂 Using custom config: {custom_config}")
    print(f"🌍 Environment: local")
    
    # Test the connection
    if setup.run_quick_test():
        print("✅ Custom configuration working!")
    else:
        print("❌ Custom configuration failed")
    
    print()

def example_incremental_setup():
    """Example 5: Incremental setup steps."""
    print("🔧 Example 5: Incremental Setup")
    print("=" * 50)
    
    # Initialize the setup system
    setup = NotionRelationsPythonSetup(environment="cloud")
    
    # Get database connection
    setup.connection = setup._get_database_connection()
    if not setup.connection:
        print("❌ Could not connect to database")
        return
    
    try:
        # Step 1: Setup core system
        print("📦 Step 1: Setting up core system...")
        if setup.setup_core_system():
            print("✅ Core system setup successful!")
        else:
            print("❌ Core system setup failed")
            return
        
        # Step 2: Setup automatic relations
        print("🔍 Step 2: Setting up automatic relations...")
        if setup.setup_automatic_relations():
            print("✅ Automatic relations setup successful!")
        else:
            print("❌ Automatic relations setup failed")
            return
        
        # Step 3: Verify everything
        print("🔍 Step 3: Verifying setup...")
        if setup.verify_setup():
            print("✅ All verification checks passed!")
        else:
            print("❌ Some verification checks failed")
        
        # Step 4: Run demos
        print("🎯 Step 4: Running demo queries...")
        if setup.run_demo_queries():
            print("✅ Demo queries successful!")
        else:
            print("⚠️ Demo queries failed, but setup may still be successful")
        
    finally:
        setup.connection.close()
    
    print()

def example_error_handling():
    """Example 6: Error handling and recovery."""
    print("🛡️ Example 6: Error Handling")
    print("=" * 50)
    
    try:
        # Initialize with invalid config to test error handling
        setup = NotionRelationsPythonSetup(
            config_path="nonexistent_config.json",
            environment="cloud"
        )
        
        print("❌ This should fail gracefully...")
        setup.setup_complete_system()
        
    except FileNotFoundError as e:
        print(f"✅ Error handled correctly: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    print()

def main():
    """Run all examples."""
    print("🎯 Notion Relations Python Integration Examples")
    print("=" * 60)
    print()
    
    # Run examples
    example_basic_setup()
    example_verification_only()
    example_demo_queries()
    example_custom_config()
    example_incremental_setup()
    example_error_handling()
    
    print("🎉 All examples completed!")
    print()
    print("💡 To use the system in your own code:")
    print("   from notion.notion_relations_python_setup import NotionRelationsPythonSetup")
    print("   setup = NotionRelationsPythonSetup(environment='cloud')")
    print("   setup.setup_complete_system()")

if __name__ == "__main__":
    main()