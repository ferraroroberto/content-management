# Process Module

The Process module handles data processing, transformation, and database operations for social media analytics data. It takes raw JSON data from various social media platforms, processes it according to mapping configurations, and uploads it to a Supabase PostgreSQL database.

## 🚀 Overview

This module contains several components:

1. **Data Processor** - Transforms raw JSON data into structured DataFrames
2. **Supabase Uploader** - Handles database connections and data uploads
3. **Profile Aggregator** - Consolidates profile data from multiple platforms
4. **Posts Consolidator** - Merges posts data across platforms
5. **Supabase Relations Creator** - Creates relational structure from Notion database relations
6. **Supabase Policy Script** - Applies RLS policies to all database tables
7. **Database Utilities** - Test connections and manage database operations

## 📊 Components

### 📊 data_processor.py

The main data processing engine that:
- Reads raw JSON files from the `results/raw` directory
- Applies field mappings from `config/mapping.json`
- Transforms nested JSON structures into flat DataFrames
- Handles different data types (posts, profiles, etc.)
- Exports processed data as CSV or Excel files
- Optionally uploads data to Supabase

**Key Features:**
- Automatic type conversion (dates, booleans, integers)
- Nested field extraction using dot notation
- Array data processing for posts
- Missing field validation
- Debug mode for detailed logging

### 📤 supabase_uploader.py

Database interface module that:
- Manages PostgreSQL connections (local/cloud environments)
- Creates tables automatically based on DataFrame structure
- Handles upsert operations with primary key conflict resolution
- Supports batch uploads for large datasets
- Provides connection pooling and error handling

**Key Features:**
- Environment-based configuration (local/cloud)
- Automatic table creation with appropriate data types
- Primary key determination based on data type
- Batch processing to avoid memory issues
- Transaction management

### 🔄 profile_aggregator.py

Consolidates follower counts from all platforms into a single `profile` table:
- Reads from individual platform profile tables
- Creates a unified view with all follower counts
- Maintains historical data by date
- Uses SQL for efficient aggregation

### 📝 posts_consolidator.py

Merges posts data from all platforms:
- Separates video and non-video content
- Creates a wide-format table with all platform data
- Links posts to their URLs
- Filters for posts from the previous day

### 🔗 supabase_relations_creator.py

Creates relational structure in Supabase from Notion database relations:
- Loads Notion database list and relations data from JSON files
- Creates junction tables for many-to-many relationships
- Supports both local and cloud database environments
- Provides dry-run mode for previewing changes
- Handles table deduplication and cleanup operations

**Key Features:**
- Automatic junction table creation based on relation configurations
- Support for self-referential relationships
- Bidirectional relationship handling with option for deduplication
- Comprehensive logging with emoji indicators
- Environment-based configuration management
- Dry-run mode for safe testing

**Command Line Options:**
- `--environment`: Choose between local/cloud database (default: cloud)
- `--dry-run`: Preview changes without executing them
- `--drop-all`: Remove all tables without recreating them
- `--debug`: Enable detailed debug logging
- `--de-duplicate`: Deduplicate junction tables (default: False)

### 🔒 supabase_policy_script.py

Applies Row Level Security (RLS) policies to all existing tables in Supabase:
- Scans all tables in the public schema
- Enables RLS on tables that don't have it
- Creates essential policies for anonymous access (SELECT, INSERT, UPDATE, DELETE)
- Handles existing policies gracefully to avoid conflicts
- Provides comprehensive reporting on policy application status

**Key Features:**
- Automatic detection of existing policies and RLS status
- Smart policy application (only creates what's missing)
- Force mode to drop and recreate all policies
- Dry-run mode to preview changes without execution
- Comprehensive logging with emoji indicators
- Environment-based configuration management
- Error handling and recovery for individual table failures

**Command Line Options:**
- `--environment`: Choose between local/cloud database (default: cloud)
- `--dry-run`: Preview what would be done without executing
- `--force`: Drop existing policies before creating new ones
- `--debug`: Enable detailed debug logging

### 🧪 Database Test Utilities

- `supabase_test_connect.py` - Test database connectivity
- `supabase_test_create_table.py` - Create test tables
- `supabase_drop_all_tables.py` - Clean up database

## ⚙️ Setup

### 📋 Prerequisites

- Python 3.x
- PostgreSQL database (local or Supabase cloud)
- Required Python packages:
  ```bash
  pip install pandas psycopg2-binary python-dotenv argparse
  ```

### 🔧 Environment Configuration

1. Copy `.env_example` to `.env`:
   ```bash
   cp .env_example .env
   ```

2. Update `.env` with your database credentials:
   ```env
   # For local development
   db_user_local=postgres
   db_password_local=your_password
   db_host_local=127.0.0.1
   db_port_local=5432
   db_name_local=postgres

   # For cloud deployment
   db_user_cloud=your_cloud_user
   db_password_cloud=your_cloud_password
   db_host_cloud=your_host.supabase.com
   db_port_cloud=5432
   db_name_cloud=postgres
   ```

### 📁 Configuration Files

The process module relies on configuration files in the `../config` directory:

1. **config.json** - Main configuration with API settings and folder paths
2. **mapping.json** - Field mapping rules for data transformation

## 🚀 Usage

### 📊 Basic Data Processing

Process all JSON files and create DataFrames:

```bash
python data_processor.py
```

### 🔗 Creating Database Relations

Create relational structure from Notion database relations:

```bash
# Preview changes without executing
python supabase_relations_creator.py --dry-run

# Create relations in cloud environment
python supabase_relations_creator.py --environment cloud

# Enable debug mode for detailed logging
python supabase_relations_creator.py --debug

# Deduplicate junction tables
python supabase_relations_creator.py --de-duplicate

# Drop all tables (use with caution)
python supabase_relations_creator.py --drop-all
```

### 🔒 Applying RLS Policies

Apply Row Level Security policies to all existing tables:

```bash
# Preview what would be done without executing
python supabase_policy_script.py --dry-run

# Apply policies to all tables (skips tables that already have policies)
python supabase_policy_script.py

# Force apply policies (drops existing policies first)
python supabase_policy_script.py --force

# Use local environment
python supabase_policy_script.py --environment local

# Enable debug logging
python supabase_policy_script.py --debug
```

### 📝 Command Line Options

#### data_processor.py

```bash
# Enable debug mode
python data_processor.py --debug

# Skip upload to database
python data_processor.py --upload n

# Export as Excel instead of CSV
python data_processor.py --format excel

# All options combined
python data_processor.py --debug --upload n --format excel
```

#### supabase_uploader.py

```bash
# Test with a CSV file (cloud environment)
python supabase_uploader.py --csv path/to/file.csv

# Use local database
python supabase_uploader.py --environment local --csv path/to/file.csv
```

#### profile_aggregator.py

```bash
# Run profile aggregation
python profile_aggregator.py

# Debug mode
python profile_aggregator.py --debug
```

#### posts_consolidator.py

```bash
# Run posts consolidation
python posts_consolidator.py

# Debug mode
python posts_consolidator.py --debug
```

## 📊 Data Flow

1. **Input**: Raw JSON files in `results/raw/` directory
   - Format: `{platform}_{datatype}_{YYYY-MM-DD}.json`

2. **Processing**: 
   - Field extraction based on mapping rules
   - Type conversion and validation
   - DataFrame creation

3. **Output**:
   - CSV/Excel files in `results/processed/`
   - Database tables in Supabase
   - Relational structure with junction tables

## 🗄️ Database Schema

### 🔑 Primary Keys by Data Type

- **Posts**: `date`, `platform`, `data_type`, `post_id`
- **Profile**: `date`, `platform`, `data_type`
- **Comments**: `comment_id`
- **Insights/Metrics**: `post_id`, `date`

### 🔗 Aggregated Tables

- **profile**: Consolidated follower counts across all platforms
- **posts**: Unified posts data with video/non-video separation

### 🔗 Junction Tables

- **{table1}_to_{table2}**: Many-to-many relationships between different tables
- **{table}_relations**: Self-referential relationships within the same table
- **Deduplication**: Option to create single junction table for bidirectional relationships

## ❌ Error Handling

The module includes comprehensive error handling:

- Missing configuration files
- Invalid JSON data
- Database connection failures
- Missing required fields
- Type conversion errors
- Network timeouts
- Relation creation failures

All errors are logged with descriptive messages and emoji indicators.

## 📝 Logging

Uses custom logger with:
- Console output with emoji indicators
- Debug/Info level switching
- File logging (optional)
- Progress tracking for batch operations

### 📊 Log Indicators:
- 🚀 Starting/Processing
- ✅ Success
- ❌ Error
- ⚠️ Warning
- 📂 File operations
- 📤 Upload operations
- 🔄 Aggregation operations
- 📊 Progress updates
- 🐞 Debug mode
- 🔗 Relation operations
- 🗑️ Cleanup operations

## 🔧 Development

### ➕ Adding New Platforms

1. Update `mapping.json` with field mappings
2. Ensure raw data follows standard format
3. Run data processor to test transformation
4. Verify database table creation

### 🔗 Adding New Relations

1. Update Notion database relations configuration
2. Run relations creator in dry-run mode to preview
3. Execute relations creation
4. Verify junction table structure

### 🚀 Extending Functionality

The module is designed to be extensible:
- Add new data types in `get_primary_keys()`
- Create custom field transformations in mapping
- Add new aggregation SQL files
- Implement additional consolidators
- Extend relation creation logic

## 🐛 Troubleshooting

### ❗ Common Issues

1. **Connection Failed**
   - Check `.env` file exists and has correct credentials
   - Verify database is accessible
   - Check network/firewall settings

2. **Missing Fields**
   - Review mapping.json for correct paths
   - Check if API response structure changed
   - Enable debug mode for detailed logs

3. **Type Conversion Errors**
   - Verify data types in mapping configuration
   - Check for null/missing values
   - Review date format handling

4. **Relation Creation Failures**
   - Verify Notion database list and relations files exist
   - Check database IDs match between files
   - Review junction table naming conflicts

5. **Policy Application Issues**
   - Ensure database user has sufficient privileges
   - Check for existing policy conflicts
   - Use dry-run mode to preview changes
   - Review table permissions and ownership

### 🔍 Debug Mode

Enable debug mode for detailed logging:
```bash
python data_processor.py --debug
python supabase_relations_creator.py --debug
```

This provides:
- Step-by-step execution logs
- Raw data samples
- SQL queries being executed
- Detailed error messages
- Relation creation breakdown
- Junction table analysis
