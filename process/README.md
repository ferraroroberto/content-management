# Process Module

The Process module handles data processing, transformation, and database operations for social media analytics data. It takes raw JSON data from various social media platforms, processes it according to mapping configurations, and uploads it to a Supabase PostgreSQL database.

## Overview

This module contains several components:

1. **Data Processor** - Transforms raw JSON data into structured DataFrames
2. **Supabase Uploader** - Handles database connections and data uploads
3. **Profile Aggregator** - Consolidates profile data from multiple platforms
4. **Posts Consolidator** - Merges posts data across platforms
5. **Database Utilities** - Test connections and manage database operations

## Components

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

### 🧪 Database Test Utilities

- `supabase_test_connect.py` - Test database connectivity
- `supabase_test_create_table.py` - Create test tables
- `supabase_drop_all_tables.py` - Clean up database

## Setup

### Prerequisites

- Python 3.x
- PostgreSQL database (local or Supabase cloud)
- Required Python packages:
  ```bash
  pip install pandas psycopg2-binary python-dotenv argparse
  ```

### Environment Configuration

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

### Configuration Files

The process module relies on configuration files in the `../config` directory:

1. **config.json** - Main configuration with API settings and folder paths
2. **mapping.json** - Field mapping rules for data transformation

## Usage

### Basic Data Processing

Process all JSON files and create DataFrames:

```bash
python data_processor.py
```

### Command Line Options

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

## Data Flow

1. **Input**: Raw JSON files in `results/raw/` directory
   - Format: `{platform}_{datatype}_{YYYY-MM-DD}.json`

2. **Processing**: 
   - Field extraction based on mapping rules
   - Type conversion and validation
   - DataFrame creation

3. **Output**:
   - CSV/Excel files in `results/processed/`
   - Database tables in Supabase

## Database Schema

### Primary Keys by Data Type

- **Posts**: `date`, `platform`, `data_type`, `post_id`
- **Profile**: `date`, `platform`, `data_type`
- **Comments**: `comment_id`
- **Insights/Metrics**: `post_id`, `date`

### Aggregated Tables

- **profile**: Consolidated follower counts across all platforms
- **posts**: Unified posts data with video/non-video separation

## Error Handling

The module includes comprehensive error handling:

- Missing configuration files
- Invalid JSON data
- Database connection failures
- Missing required fields
- Type conversion errors
- Network timeouts

All errors are logged with descriptive messages and emoji indicators.

## Logging

Uses custom logger with:
- Console output with emoji indicators
- Debug/Info level switching
- File logging (optional)
- Progress tracking for batch operations

### Log Indicators:
- 🚀 Starting/Processing
- ✅ Success
- ❌ Error
- ⚠️ Warning
- 📂 File operations
- 📤 Upload operations
- 🔄 Aggregation operations
- 📊 Progress updates
- 🐞 Debug mode

## Development

### Adding New Platforms

1. Update `mapping.json` with field mappings
2. Ensure raw data follows standard format
3. Run data processor to test transformation
4. Verify database table creation

### Extending Functionality

The module is designed to be extensible:
- Add new data types in `get_primary_keys()`
- Create custom field transformations in mapping
- Add new aggregation SQL files
- Implement additional consolidators

## Troubleshooting

### Common Issues

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

### Debug Mode

Enable debug mode for detailed logging:
```bash
python data_processor.py --debug
```

This provides:
- Step-by-step execution logs
- Raw data samples
- SQL queries being executed
- Detailed error messages
