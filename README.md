# Social Media Automation Suite

A comprehensive Python-based automation system for collecting, processing, and analyzing social media data across multiple platforms. This suite integrates with various APIs to fetch social media metrics, processes the data, stores it in a PostgreSQL database (Supabase), and syncs with Notion for reporting and analysis.

## 🚀 Overview

This automation suite consists of three main modules that work together to create a complete social media analytics pipeline:

1. **Social Client** - Fetches data from social media platform APIs
2. **Process** - Transforms and uploads data to PostgreSQL/Supabase
3. **Notion** - Syncs data with Notion databases for reporting

## 📊 Supported Platforms

- LinkedIn (Profile & Posts)
- Instagram (Profile & Posts)
- Twitter/X (Profile & Posts)
- Threads (Profile & Posts)
- Substack (Profile & Posts)

## 🏗️ Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Social APIs    │────▶│ Data Processing │────▶│    Supabase     │
│   (RapidAPI)    │     │   & Transform   │     │   PostgreSQL    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │     Notion      │
                                                 │   Databases     │
                                                 └─────────────────┘
```

## 📁 Project Structure

```
social-media-automation-suite/
├── 🚀 launch.py                    # Main application launcher
├── 🖥️ cli/                         # Command Line Interface
│   ├── __init__.py                # CLI package initialization
│   ├── main.py                    # Advanced CLI interface
│   ├── config.py                  # CLI configuration management
│   └── README.md                  # CLI documentation
├── 📡 social_client/               # Social Media API Clients
│   ├── social_api_client.py       # Multi-platform API client
│   └── README.md                  # API client documentation
├── 🔄 process/                     # Data Processing & Database Operations
│   ├── pipeline.py                # Pipeline orchestrator (moved from init.py)
│   ├── data_processor.py          # Data transformation engine
│   ├── supabase_uploader.py       # Database upload operations
│   ├── profile_aggregator.py      # Profile data aggregation
│   ├── posts_consolidator.py      # Posts data consolidation
│   └── README.md                  # Processing documentation
├── 📘 notion/                      # Notion Integration
│   ├── notion_update.py           # Notion database updates
│   ├── notion_supabase_sync.py    # Notion-Supabase synchronization
│   ├── notion_database_structure.py # Database structure management
│   └── README.md                  # Notion integration documentation
├── ⚙️ config/                      # Configuration & Settings
│   ├── config_example.json        # Example configuration
│   ├── mapping.json               # Field mapping definitions
│   ├── logger_config.py           # Logging configuration
│   └── README.md                  # Configuration documentation
├── 📚 docs/                       # Additional Documentation
│   ├── DATA_STRUCTURE_DOCUMENTATION.md
│   └── SUPABASE_SCHEMA.md
├── 📦 requirements.txt            # Python dependencies
└── 📖 README.md                   # This file
```

## 🚀 Quick Start

### Prerequisites

- Python 3.7+
- PostgreSQL database (local or Supabase cloud)
- API keys for social media platforms (via RapidAPI)
- Notion API token (for Notion integration)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd automation/reporting
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   # Copy example configurations
   cp config/config_example.json config/config.json
   cp process/.env_example process/.env
   
   # Edit config files with your credentials
   ```

4. **Set up database**
   - Create a Supabase project or set up local PostgreSQL
   - Update database credentials in `.env` file

### Basic Usage

#### 🚀 **Simple Launcher (Recommended for most users)**
```bash
# Run complete pipeline
python3 launch.py

# Run with debug mode
python3 launch.py --debug

# Skip specific steps
python3 launch.py --skip-api --skip-processing
```

#### 🖥️ **Advanced CLI Interface (For automation and advanced users)**
```bash
# Run complete pipeline
python3 -m cli.main

# Run only specific components
python3 -m cli.main --api-only
python3 -m cli.main --process-only
python3 -m cli.main --notion-only

# Advanced options
python3 -m cli.main --debug --date 20241201 --quiet
```

#### 🔧 **Individual Module Execution (For development and testing)**
```bash
# Collect social media data
python3 social_client/social_api_client.py

# Process and upload data
python3 process/data_processor.py

# Aggregate profiles and posts
python3 process/profile_aggregator.py
python3 process/posts_consolidator.py

# Sync with Notion
python3 notion/notion_update.py 20241201
```

## 🆕 **New Project Structure (v2.0)**

The project has been reorganized for better maintainability and user experience:

### **What Changed**
- ✅ **`init.py` → `process/pipeline.py`** - Pipeline orchestrator moved to process domain
- ✅ **New `launch.py`** - Simple, user-friendly main launcher
- ✅ **New `cli/` package** - Advanced CLI interface for automation
- ✅ **Better organization** - Clear separation of concerns

### **Migration Guide**
If you were using the old `init.py` file:
```bash
# Old way (still works)
python3 init.py

# New way (recommended)
python3 launch.py

# Advanced CLI (for automation)
python3 -m cli.main
```

### **Benefits of New Structure**
- 🚀 **Clearer entry points** - Know exactly where to start
- 🖥️ **Better CLI experience** - Advanced options for power users
- 📁 **Logical organization** - Related functionality grouped together
- 🔧 **Easier maintenance** - Clear module purposes and dependencies

---

## 📋 Module Documentation

### [Social Client Module](social_client/README.md)

Fetches data from social media APIs:
- Automatic timestamp-based file naming
- Skip existing data to avoid duplicate API calls
- Debug mode for troubleshooting
- Progress tracking and comprehensive logging

**Key Features:**
- 🚀 Multi-platform support
- 💾 JSON output with metadata
- 🔄 Smart caching
- 🐞 Debug mode

### [Process Module](reporting/process/README.md)

Handles data transformation and database operations:
- Field mapping based on configuration
- Automatic type conversion
- Database table creation
- Batch processing for large datasets

**Key Components:**
- 📊 `data_processor.py` - Main processing engine
- 📤 `supabase_uploader.py` - Database interface
- 🔄 `profile_aggregator.py` - Consolidates follower counts
- 📝 `posts_consolidator.py` - Merges posts data

### [Notion Module](reporting/notion/README.md)

Integrates with Notion for reporting:
- Bidirectional sync with Supabase
- Automatic schema detection
- Change tracking and logging
- Support for all Notion property types

**Key Tools:**
- 📝 `notion_update.py` - Updates Notion with Supabase data
- 🔄 `notion_supabase_sync.py` - Continuous database sync
- 📊 `notion_database_structure.py` - Schema analysis

### [Configuration](reporting/config/README.md)

Central configuration management:
- API credentials and endpoints
- Field mapping rules
- Database settings
- Logging configuration

## 🔄 Typical Workflow

### Daily Data Collection

```bash
# 1. Fetch latest social media data
cd reporting/social_client
python social_api_client.py

# 2. Process and upload to database
cd ../process
python data_processor.py

# 3. Aggregate data
python profile_aggregator.py
python posts_consolidator.py

# 4. Update Notion (if needed)
cd ../notion
python notion_update.py $(date +%Y%m%d)
```

### Continuous Notion Sync

```bash
# Run continuous sync in background
cd reporting/notion
python notion_supabase_sync.py
```

## 📊 Data Schema

### Profile Data
- `date`: Collection date
- `platform`: Social media platform
- `num_followers_*`: Follower count per platform

### Posts Data
- `date`: Collection date
- `platform`: Source platform
- `post_id`: Unique identifier
- `posted_at`: Publication timestamp
- `is_video`: Video content flag
- `num_likes`: Engagement metrics
- `num_comments`: Comment count
- `num_reshares`: Share/repost count

## 🔧 Advanced Configuration

### Debug Mode

Most scripts support debug mode for detailed logging:
```bash
python script_name.py --debug
```

### Environment-Specific Settings

Switch between local and cloud databases:
```bash
python supabase_uploader.py --environment local
```

### Custom Configurations

Override default configuration files:
```bash
python notion_supabase_sync.py --config custom_config.json
```

## 🐛 Troubleshooting

### Common Issues

1. **API Rate Limits**
   - Use `--skip-existing` flag to avoid re-fetching data
   - Implement delays between API calls

2. **Database Connection Errors**
   - Verify credentials in `.env` file
   - Check network connectivity
   - Ensure database is accessible

3. **Missing Data Fields**
   - Review `mapping.json` for correct field paths
   - Enable debug mode to see raw API responses
   - Check if API response structure changed

4. **Notion Sync Issues**
   - Verify Notion API token is valid
   - Check database IDs in configuration
   - Review Notion API rate limits

### Debug Commands

```bash
# Test database connection
cd reporting/process
python supabase_test_connect.py

# Analyze Notion database structure
cd ../notion
python notion_database_structure.py --debug

# Process single platform
cd ../social_client
python social_api_client.py --platform linkedin_profile --debug
```

## 📈 Performance Optimization

- **Batch Processing**: Data is processed in batches to handle large datasets
- **Incremental Sync**: Only new/modified data is synced to avoid redundant operations
- **Connection Pooling**: Database connections are pooled for efficiency
- **Smart Caching**: API responses are cached daily to minimize API calls

## 🔐 Security Best Practices

1. **Never commit sensitive data**
   - Keep `config.json` out of version control
   - Use `.env` files for database credentials
   - Rotate API keys regularly

2. **Use environment variables in production**
   ```bash
   export SUPABASE_URL="your-url"
   export SUPABASE_KEY="your-key"
   ```

3. **Implement access controls**
   - Use read-only database users where possible
   - Limit API key permissions
   - Enable Supabase Row Level Security (RLS)

## 🚧 Development

### Adding New Platforms

1. **Update configuration**
   - Add platform config to `config.json`
   - Define field mappings in `mapping.json`

2. **Test data collection**
   ```bash
   python social_api_client.py --platform new_platform --debug
   ```

3. **Verify processing**
   ```bash
   python data_processor.py --debug
   ```

### Extending Functionality

- Create custom processors in the `process` module
- Add new Notion property type handlers
- Implement additional aggregation queries

## 📝 License and contact

This project is free software for personal use from Roberto Ferraro 😇

https://www.linkedin.com/in/ferraroroberto/

Built with ❤️ for automated social media analytics and reporting
