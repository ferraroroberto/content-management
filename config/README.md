# Configuration Directory

This directory contains configuration files for the social media reporting automation system.

## Files Overview

### `config.json` / `config_example.json`

Contains API connection settings for various social media platforms:

- **LinkedIn**: Profile and posts data retrieval
- **Instagram**: Profile and posts data retrieval
- **Twitter**: Profile and posts data retrieval
- **Threads**: Profile and posts data retrieval
- **Substack**: Profile and posts data retrieval

To set up your configuration:
1. Copy `config_example.json` to a new file named `config.json`
2. Replace all instances of `your_api_key_here` with your actual API keys
3. Update usernames and other parameters in the querystring objects

### `mapping.json`

Defines how to extract and transform data from API responses. For each platform, it specifies:

- **Field paths**: Where to find specific data in the API response JSON
- **Data types**: How to interpret the extracted values (integer, string, etc.)
- **Required fields**: Which fields are mandatory
- **Transformations**: Custom transformations for specific fields
- **Array handling**: How to process arrays of posts/content

### `logger_config.py`

Provides a utility function for setting up consistent logging across the application:

- Creates console and file loggers
- Configures log formatting
- Manages log file storage

## Usage

These configuration files are used by the reporting system to:
1. Connect to social media APIs
2. Extract relevant metrics from responses
3. Transform data into a consistent format
4. Generate reports based on the collected data

Make sure to keep your API keys secure and never commit `config.json` with actual API keys to version control.
