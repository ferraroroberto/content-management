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

## 📈 Data Flow & Processing

### Complete Data Pipeline

1. **Data Collection** 📥
   - Raw JSON data fetched from social media APIs via RapidAPI
   - Daily collection frequency with smart caching
   - Raw responses stored in `results/raw/` directory
   - Skip existing data to avoid duplicate API calls

2. **Data Processing** 🔄
   - JSON data transformed using configurable field mappings
   - Automatic type conversion and validation
   - Required fields validated before processing
   - Error handling for missing or invalid data

3. **Database Storage** 💾
   - Processed data uploaded to PostgreSQL (Supabase)
   - Automatic table creation based on DataFrame structure
   - Batch processing for large datasets
   - Upsert logic (update existing, insert new)

4. **Data Aggregation** 📊
   - Profile data consolidated across platforms
   - Posts data aggregated by content type (video vs non-video)
   - One record per day with all platform data
   - Optimized for time-series analysis

5. **Reporting & Sync** 📋
   - Data synced to Notion databases for visualization
   - Bidirectional sync capabilities
   - Change tracking and comprehensive logging
   - Support for all Notion property types

### Data Collection Process

- **Frequency**: Daily automated collection
- **Platforms**: LinkedIn, Instagram, Twitter/X, Threads, Substack
- **Data Types**: Profile information and recent posts performance
- **Storage**: Raw JSON files with metadata
- **Validation**: Field mapping and type conversion
- **Quality**: Comprehensive error handling and logging

## 📁 Project Structure

```
automation/
├── reporting/
│   ├── social_client/         # API data collection
│   │   ├── social_api_client.py
│   │   └── README.md
│   ├── process/               # Data processing & database operations
│   │   ├── data_processor.py
│   │   ├── supabase_uploader.py
│   │   ├── profile_aggregator.py
│   │   ├── posts_consolidator.py
│   │   └── README.md
│   ├── notion/                # Notion integration
│   │   ├── notion_update.py
│   │   ├── notion_supabase_sync.py
│   │   ├── notion_database_structure.py
│   │   └── README.md
│   ├── config/                # Configuration files
│   │   ├── config.json
│   │   ├── mapping.json
│   │   ├── logger_config.py
│   │   └── README.md
│   └── results/               # Output directories
│       ├── raw/               # Raw API responses
│       └── processed/         # Processed data files
└── README.md
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

1. **Collect social media data**
   ```bash
   cd social_client
   python social_api_client.py
   ```

2. **Process and upload data**
   ```bash
   cd ../process
   python data_processor.py
   ```

3. **Aggregate profiles and posts**
   ```bash
   python profile_aggregator.py
   python posts_consolidator.py
   ```

4. **Sync with Notion (optional)**
   ```bash
   cd ../notion
   python notion_update.py YYYYMMDD
   ```

## 📋 Module Documentation

### [Social Client Module](reporting/social_client/README.md)

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

## 📊 Data Schema & Structure

### Database Architecture

The system uses PostgreSQL (via Supabase) with a normalized schema that separates raw data collection from aggregated analytics. All tables use `date` as the primary key for efficient time-series queries.

### Raw Data Tables

The system creates individual tables for each platform and data type to store raw API responses:

#### Profile Tables
- **`linkedin_profile`**: LinkedIn follower counts and profile data
- **`instagram_profile`**: Instagram follower counts and profile data
- **`twitter_profile`**: Twitter/X follower counts and profile data
- **`threads_profile`**: Threads follower counts and profile data
- **`substack_profile`**: Substack subscriber counts and profile data

**Common Profile Fields:**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier
- `data_type` (text): Data type identifier ('profile')
- `num_followers` (integer): Number of followers/subscribers

#### Posts Tables
- **`linkedin_posts`**: LinkedIn post performance metrics
- **`instagram_posts`**: Instagram post performance metrics
- **`twitter_posts`**: Twitter/X post performance metrics
- **`threads_posts`**: Threads post performance metrics
- **`substack_posts`**: Substack post performance metrics

**Common Posts Fields:**
- `date` (date, PRIMARY KEY): Date of data collection
- `platform` (text): Platform identifier
- `data_type` (text): Data type identifier ('posts')
- `post_id` (text): Unique post identifier
- `posted_at` (date): Date when post was published
- `is_video` (integer): Boolean flag (1 for video, 0 for non-video)
- `num_likes` (integer): Number of likes/reactions
- `num_comments` (integer): Number of comments
- `num_reshares` (integer): Number of reshares/reposts

### Aggregated Tables

#### Profile Summary Table
**`profile`** - Consolidated daily follower counts across all platforms:
- `date` (date, PRIMARY KEY): Date of data collection
- `num_followers_linkedin` (integer): LinkedIn follower count
- `num_followers_instagram` (integer): Instagram follower count
- `num_followers_twitter` (integer): Twitter follower count
- `num_followers_substack` (integer): Substack subscriber count
- `num_followers_threads` (integer): Threads follower count

#### Posts Summary Table
**`posts`** - Daily post performance metrics separated by content type:
- `date` (date, PRIMARY KEY): Date of data collection

**Non-Video Posts (by platform):**
- `post_id_*_no_video`: Post ID for latest non-video content
- `posted_at_*_no_video`: Publication date
- `num_likes_*_no_video`: Engagement metrics
- `num_comments_*_no_video`: Comment counts
- `num_reshares_*_no_video`: Share counts

**Video Posts (by platform):**
- `post_id_*_video`: Post ID for latest video content
- `posted_at_*_video`: Publication date
- `num_likes_*_video`: Engagement metrics
- `num_comments_*_video`: Comment counts
- `num_reshares_*_video`: Share counts

*(* = linkedin, instagram, twitter, substack, threads)

## 🗄️ Database Architecture & Integration

### Two-Stage Data Pipeline

The system implements a sophisticated two-stage data pipeline designed for scalability and analysis:

#### Stage 1: Raw Data Ingestion
**Social Media Data:**
- Platform-specific tables store raw API responses
- Automatic table creation based on data structure
- Preserves original data integrity before transformation

**Notion Integration:**
- Dynamic schema detection from Notion databases
- Bidirectional sync with change tracking
- Complex data types stored as JSONB for flexibility

#### Stage 2: Data Consolidation
- SQL aggregation scripts process raw data into analysis-ready tables
- Platform-specific data merged into unified views
- Optimized for time-series analysis and cross-platform comparisons

### Notion Database Integration

#### Common Notion Table Structure
All Notion-synced tables share standardized columns:

| Column | Data Type | Description |
| :--- | :--- | :--- |
| `notion_id` | `text` | Notion page ID (UUID) - **Primary Key** |
| `created_time` | `timestamp with time zone` | When page was created in Notion |
| `last_edited_time` | `timestamp with time zone` | When page was last edited |
| `archived` | `boolean` | Whether page is archived |
| `notion_data_jsonb` | `jsonb` | Complex data types and unmapped properties |

#### Dynamic Schema Generation
Tables are automatically created with columns derived from Notion properties:
- **Property names** → normalized column names (lowercase, underscores)
- **Data types** automatically mapped from Notion to PostgreSQL
- **Complex types** (relations, arrays) stored in JSONB column

#### Notion to PostgreSQL Type Mapping

| Notion Property Type | PostgreSQL Data Type |
| :--- | :--- |
| Title, Rich Text, URL, Email, Phone | `text` |
| Number | `bigint` or `double precision` |
| Select, Status | `text` |
| Date | `timestamp with time zone` |
| Checkbox | `boolean` |
| Formula (various) | Mapped to appropriate types |
| Multi-Select, Relation, People, Files | `jsonb` |

### Integrated Notion Databases

The system syncs data from **15+ Notion databases** for comprehensive content management:

**Content & Publishing:**
- `notion_posts` - Social media posts and content
- `notion_articles` - Blog articles and written content
- `notion_newsletter` - Newsletter content and campaigns

**Media & Assets:**
- `notion_clips` - Video/audio clips and media assets
- `notion_illustrations` - Images and visual content
- `notion_visual_types` - Media categorization

**Business & Analytics:**
- `notion_companies` - Company profiles and relationships
- `notion_connections` - Network and relationship data
- `notion_interactions` - User engagement and interactions

**Content Strategy:**
- `notion_editorial` - Editorial calendar and planning
- `notion_concepts` - Content ideas and brainstorming
- `notion_books` - Book recommendations and reviews
- `notion_books_recommendations` - Reading lists and suggestions

**Additional Databases:**
- `notion_episodes` - Podcast episodes and series
- `notion_comments` - User comments and feedback
- `notion_wins_and_features` - Success metrics and feature tracking

### Database Relationships & Constraints

**Social Media Data:**
- Raw platform tables feed into consolidated tables
- Foreign key relationships based on `date` field
- No traditional foreign keys between Notion tables

**Notion Data:**
- Relationships stored as Notion page ID arrays in JSONB
- Application-layer joins required for complex queries
- Preserves Notion's flexible relationship model

**Data Integrity:**
- Primary keys ensure uniqueness
- Timestamp tracking for change detection
- Archive status management for data lifecycle

## 📈 Analytics & Query Examples

### Growth Analysis Queries

**Monthly follower growth by platform:**
```sql
SELECT
    DATE_TRUNC('month', date) as month,
    platform,
    AVG(num_followers) as avg_followers,
    MAX(num_followers) - MIN(num_followers) as growth
FROM (
    SELECT date, 'linkedin' as platform, num_followers_linkedin as num_followers FROM profile
    UNION ALL
    SELECT date, 'instagram' as platform, num_followers_instagram as num_followers FROM profile
    UNION ALL
    SELECT date, 'twitter' as platform, num_followers_twitter as num_followers FROM profile
) combined
GROUP BY month, platform
ORDER BY month, platform;
```

### Engagement Analysis

**Average engagement by content type and platform:**
```sql
SELECT
    'linkedin' as platform,
    'video' as content_type,
    AVG(num_likes_linkedin_video) as avg_likes,
    AVG(num_comments_linkedin_video) as avg_comments,
    AVG(num_reshares_linkedin_video) as avg_reshares
FROM posts
WHERE num_likes_linkedin_video IS NOT NULL;
```

### Performance Prediction Features

**Features for ML model training:**
```sql
SELECT
    p.date,
    -- Historical performance (7-day average)
    AVG(ps.num_likes_linkedin_no_video) OVER (
        ORDER BY p.date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
    ) as avg_likes_7d,
    -- Growth momentum
    p.num_followers_linkedin - LAG(p.num_followers_linkedin, 7) OVER (ORDER BY p.date) as follower_growth_7d,
    -- Engagement rate
    CASE
        WHEN p.num_followers_linkedin > 0
        THEN (ps.num_likes_linkedin_no_video + ps.num_comments_linkedin_no_video) / p.num_followers_linkedin
        ELSE NULL
    END as engagement_rate
FROM profile p
LEFT JOIN posts ps ON p.date = ps.date
WHERE ps.num_likes_linkedin_no_video IS NOT NULL;
```

### Cross-Platform Analysis

**Total audience reach across platforms:**
```sql
SELECT
    date,
    num_followers_linkedin + num_followers_instagram + num_followers_twitter +
    num_followers_substack + num_followers_threads as total_followers,
    -- Engagement rates
    CASE WHEN num_followers_linkedin > 0
         THEN (num_likes_linkedin_no_video + num_comments_linkedin_no_video) / num_followers_linkedin
         ELSE 0 END as linkedin_engagement_rate,
    CASE WHEN num_followers_instagram > 0
         THEN (num_likes_instagram_no_video + num_comments_instagram_no_video) / num_followers_instagram
         ELSE 0 END as instagram_engagement_rate
FROM profile p
LEFT JOIN posts ps ON p.date = ps.date
ORDER BY date DESC;
```

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
