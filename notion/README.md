# Notion Automation Tools

This folder contains a collection of Python scripts for automating various Notion database operations, including syncing with Supabase, updating database entries, and analyzing database structures.

## Overview

The Notion automation suite consists of three main tools:

1. **notion_update.py** - Updates Notion database entries with data from Supabase
2. **notion_supabase_sync.py** - Continuously syncs Notion databases to Supabase PostgreSQL
3. **notion_database_structure.py** - Analyzes and exports Notion database structures

## Prerequisites

- Python 3.7+
- Notion API token
- PostgreSQL database (Supabase)
- Required Python packages (see requirements)

## Installation

1. Install required packages:
```bash
pip install notion-client psycopg2-binary pandas python-dotenv requests
```

2. Configure your environment:
   - Copy `.env.example` to `.env` and fill in your credentials
   - Update `config/config.json` with your Notion API token and database settings

## Configuration

### config.json Structure

```json
{
  "notion": {
    "api_token": "your-notion-api-token",
    "databases": [
      {
        "id": "database-id",
        "name": "database-name"
      }
    ],
    "poll_every": 300,
    "page_size": 100,
    "update_fields_followers": ["field1", "field2"],
    "update_fields_posts": ["field3", "field4"],
    "update_field_mapping_followers": {
      "notion_field": "supabase_table.field"
    },
    "update_field_mapping_posts": {
      "notion_field": "supabase_table.field"
    }
  },
  "supabase": {
    "posts_table": "posts",
    "profile_table": "profile"
  }
}
```

### notion_database_list.json

This file contains the list of Notion databases to sync. Each database entry includes:

```json
{
  "id": "database-id",
  "name": "database-name",
  "url": "notion-url",
  "replication": true,
  "supabase_table": "target_table_name"
}
```

Only databases with `"replication": true` will be synced to Supabase.

## Tools Documentation

### 1. notion_update.py

Updates specific Notion database entries with data from Supabase based on date matching.

**Usage:**
```bash
python notion_update.py YYYYMMDD [--debug] [--database-id DATABASE_ID]
```

**Arguments:**
- `YYYYMMDD`: Target date for updates (required)
- `--debug`: Enable debug logging
- `--database-id`: Override database ID from config

**Example:**
```bash
python notion_update.py 20240115 --debug
```

**Features:**
- Updates posts fields from the previous day's Supabase data
- Updates follower fields from the current day's Supabase data
- Tracks all changes in a `notion_tracking` table
- Interactive confirmation before applying updates

### 2. notion_supabase_sync.py

Continuously syncs Notion databases to Supabase PostgreSQL tables.

**Usage:**
```bash
python notion_supabase_sync.py [--environment ENV] [--once] [--full-sync]
```

**Arguments:**
- `--environment`: Database environment (`local` or `cloud`, default: `cloud`)
- `--once`: Run sync once and exit (default: continuous)
- `--full-sync`: Force a full sync, ignoring last sync time
- `--config`: Path to custom configuration file
- `--database-list`: Path to custom database list file

**Example:**
```bash
# Run continuous sync
python notion_supabase_sync.py

# Run once with full sync
python notion_supabase_sync.py --once --full-sync

# Use local database
python notion_supabase_sync.py --environment local
```

**Features:**
- Incremental sync based on last_edited_time
- Automatic table creation and schema updates
- Handles all Notion property types
- Rate limiting to respect Notion API limits
- Batch processing for efficient database updates

### 3. notion_database_structure.py

Analyzes Notion database structures and exports sample data.

**Usage:**
```bash
python notion_database_structure.py [--debug]
```

**Arguments:**
- `--debug`: Enable debug logging

**Example:**
```bash
python notion_database_structure.py --debug
```

**Features:**
- Extracts database schema with all property types
- Exports structure to JSON format
- Saves sample content (100 records) to CSV
- Only processes databases marked for replication
- Outputs to `database_sample/` directory

## Database Sync Process

The sync process follows these steps:

1. **Database Discovery**: Reads `notion_database_list.json` for databases with `replication: true`
2. **Schema Detection**: Automatically detects Notion property types and creates appropriate PostgreSQL columns
3. **Data Transformation**: Converts Notion properties to PostgreSQL-compatible formats
4. **Incremental Updates**: Only syncs pages modified since last sync (unless `--full-sync` is used)
5. **Conflict Resolution**: Uses UPSERT operations to handle existing records

## Property Type Mapping

| Notion Type | PostgreSQL Type | Notes |
|-------------|-----------------|-------|
| title | text | Primary text content |
| rich_text | text | Formatted text |
| number | double precision | Numeric values |
| select | text | Single selection |
| multi_select | jsonb | Array of selections |
| date | timestamp with time zone | Date/datetime values |
| checkbox | boolean | True/false values |
| url | text | URL strings |
| email | text | Email addresses |
| phone_number | text | Phone numbers |
| formula | varies | Based on formula result |
| relation | jsonb | Array of related IDs |
| rollup | jsonb | Aggregated values |
| people | jsonb | Array of user IDs |
| files | jsonb | Array of file URLs |

## Tracking and Logging

### Change Tracking

The `notion_update.py` script creates a `notion_tracking` table to log all field changes:

```sql
CREATE TABLE notion_tracking (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notion_page_id TEXT NOT NULL,
    notion_field TEXT NOT NULL,
    notion_value_initial TEXT,
    notion_value_final TEXT
);
```

### Logging

All scripts use a centralized logging configuration with:
- Console output with emoji indicators
- Debug mode for detailed information
- Error tracking with descriptive messages

## Common Issues and Solutions

### Issue: "No row found for date"
**Solution**: Ensure the Notion database has entries for the specified date. The date format should be YYYYMMDD.

### Issue: "Failed to connect to database"
**Solution**: Check your database credentials in the environment configuration and ensure the database is accessible.

### Issue: "Rate limit exceeded"
**Solution**: The sync script includes automatic rate limiting. If issues persist, increase the delay in `_notion_api_call()`.

### Issue: "Column type mismatch"
**Solution**: The sync script automatically handles type conversions. For persistent issues, check the source data format.

## Best Practices

1. **Test First**: Always run with `--debug` flag first to verify operations
2. **Backup Data**: Ensure database backups before running full syncs
3. **Monitor Logs**: Check logs regularly for warnings or errors
4. **Incremental Syncs**: Use incremental syncs for regular updates to minimize API calls
5. **Database List**: Keep `notion_database_list.json` updated with current database IDs

## Development

### Adding New Property Types

To support new Notion property types:

1. Update `_extract_property_value()` in `notion_supabase_sync.py`
2. Add type mapping in `_get_postgres_type()`
3. Update the property type mapping table in this README

### Extending Field Mappings

To add new field mappings for `notion_update.py`:

1. Update `update_fields_*` arrays in `config.json`
2. Add field mappings in `update_field_mapping_*` objects
3. Ensure Supabase tables have the required columns

## License

This project is part of the automation suite for personal/business use.

## Support

For issues or questions, please check the logs first and ensure all configuration files are properly set up.
