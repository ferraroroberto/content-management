# Configuration Directory

This directory contains all configuration files for the social media content management automation system.

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

### 5. `console.py`
Single-source helper for forcing UTF-8 stdio on Windows (`cp1252`) consoles so emoji in log output and print statements do not crash the pipeline. Exposes one function:

```python
from config.console import force_utf8_stdio
force_utf8_stdio()   # called once at each entry-point module's top level
```

All entry points (`*_pipeline.py`, `app/*.py`, `newsletter/*.py`, etc.) call this instead of the inline `for _stream in (sys.stdout, sys.stderr): _stream.reconfigure(...)` loop that was previously hand-copied across ~13 modules.

### 6. `loader.py`
Single-source loader for `config.json`. Every module that needs the config reads it through here instead of re-implementing its own open/parse helper. Exposes two functions:

```python
from config.loader import load_full_config, load_block

cfg = load_full_config()        # full config.json, cached for the process
notion = load_block("notion")   # one top-level block, raises if missing
```

Both raise on a missing or corrupt `config.json` (config is mandatory — a clear exception beats a silent skip), matching the contracts already used by `reporting/scrape_client/base.py` and `planning/_session_base.py`. This replaced seven hand-copied `load_config()` variants scattered across `reporting/`, `engagement/`, and `newsletter/`.

### 7. `chrome_launch.py`
Shared real-Chrome launch options for every Playwright-driven platform (LinkedIn, Instagram, Twitter, Threads, Substack). Builds the kwargs that make an automated Chrome session indistinguishable from a human-driven one — real Chrome (not bundled Chromium) via `channel="chrome"`, no "Chrome is being controlled by automated test software" infobar, no detectable `navigator.webdriver`, a pinned 1280×900 viewport, and a forced `en-US` locale so English selectors don't break on a localized OS/account.

```python
from config.chrome_launch import stealth_launch_kwargs, STEALTH_INIT_SCRIPT

context = pw.chromium.launch_persistent_context(
    **stealth_launch_kwargs(str(user_data_dir), headless=False)
)
context.add_init_script(STEALTH_INIT_SCRIPT)
```

**"NEVER re-inline these arguments in a new module — that's how stealth gets out of sync across platforms. Edit this file once; everyone inherits."**

### 8. `chrome_profile_lock.py`
Serializes access to a shared persistent Chrome profile. A persistent Chrome profile allows only one live instance, and several unattended jobs in this suite target the same profile (e.g. the engagement scrape and the reporting follower-scrape both drive `planning/linkedin/chrome_user_data`). The holder is almost always a legitimately-running sibling job, not a stale zombie, so this module waits for the profile to free with exponential backoff and re-attempts the launch — it never kills the holder — raising only if the profile is still locked after the full backoff schedule.

```python
from config.chrome_profile_lock import launch_persistent_context_with_lock_wait

context = launch_persistent_context_with_lock_wait(
    playwright, user_data_dir, headless=False, logger=logger,
)
```

**"Single source of truth: every session module imports `launch_persistent_context_with_lock_wait` from here — never re-inline a launch-with-retry in a new module."**

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
