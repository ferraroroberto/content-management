# Configuration Directory

This directory contains all configuration files for the social media reporting automation system.

## Files Overview

### 1. `config.json`
Main configuration file containing API credentials and settings for various social media platforms and services.

**Structure:**
- **General Settings**
  - `folder_results_raw`: Directory for raw API responses
  - `folder_results_processed`: Directory for processed data

- **Supabase Configuration**
  - Database connection settings
  - Table names for posts and profile data
  - Upload enablement flag

- **Social Media Platform Configurations**
  Each platform (LinkedIn, Instagram, Twitter, Threads, Substack) has two sections:
  - `{platform}_profile`: Profile data API configuration
  - `{platform}_posts`: Posts data API configuration
  
  Each section contains:
  - `api_url`: API endpoint URL
  - `api_key`: RapidAPI key
  - `api_host`: API host header
  - `querystring`: Query parameters (username, user ID, etc.)

- **Notion Integration**
  - API token
  - Database configurations
  - Field mappings for updating Notion with social media metrics

### 2. `config_example.json`
Template configuration file with placeholder values. Copy this file to `config.json` and fill in your actual credentials.

### 3. `mapping.json`
Defines data extraction and transformation rules for each social media platform.

**Structure:**
- Each platform has profile and posts mapping configurations
- Field mappings include:
  - `path`: JSON path to extract data from API response
  - `type`: Data type (integer, string, boolean_exists, custom)
  - `required`: Whether the field is mandatory
  - `transform`: Optional transformation logic (for custom types)

**Supported Platforms:**
- LinkedIn (profile & posts)
- Instagram (profile & posts)
- Twitter (profile & posts)
- Threads (profile & posts)
- Substack (profile & posts)

### 4. `logger_config.py`
Python module for setting up logging configuration.

**Features:**
- Configurable logging levels
- Console and file output options
- Automatic log directory creation
- UTF-8 encoding support for file logs

## Usage

1. Copy `config_example.json` to `config.json`
2. Fill in your API credentials and settings
3. Adjust `mapping.json` if API response structures change
4. Import and use the logger configuration in your Python scripts:

```python
from config.logger_config import setup_logger

logger = setup_logger('my_module', level=logging.INFO)
```

## Security Notes

- **Never commit `config.json` to version control** - it contains sensitive API keys
- Use environment variables for production deployments
- Keep `config_example.json` updated with structure changes

## Field Mappings

The system extracts the following metrics from each platform:

### Profile Metrics
- `num_followers`: Total follower count

### Post Metrics
- `post_id`: Unique identifier for the post
- `posted_at`: Timestamp when posted
- `is_video`: Boolean indicating if post contains video
- `num_likes`: Total likes/reactions
- `num_comments`: Total comments
- `num_reshares`: Total shares/reposts (where applicable)

## Notion Integration

The configuration supports updating Notion databases with social media metrics:

- **Follower Fields**: Updates follower counts for each platform
- **Post Fields**: Updates engagement metrics for individual posts
- **Video Post Tracking**: Separate fields for video content on supported platforms
