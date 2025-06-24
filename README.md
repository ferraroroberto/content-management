# Social Media Data Retrieval Tool

This tool fetches data from various social media platforms (LinkedIn, Instagram, Twitter, Threads, Substack, and others) using APIs and saves the results to JSON files. It uses a unified API client approach to handle all platforms through a single configuration file.

## Setup

1. Make sure you have Python installed
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
3. Configuration is stored in `config/config.json`

## Usage

Run the social API client from the `reporting` directory:

```powershell
# Run the social API client
python social_client/social_api_client.py
```

The script will:
1. Ask if you want to enable debug mode (y/n)
2. Process all endpoints defined in your configuration file
3. Save results to JSON files in the `results` directory

When in debug mode:
- More detailed logs will be displayed
- You'll be prompted to continue after each endpoint is processed
- You can stop processing at any time

## Logging

The application uses a comprehensive logging system:

- Console logs: All logs are displayed in the console
- Log levels:
  - INFO: Normal operation logs (default)
  - DEBUG: Detailed debugging information (when debug mode is enabled)
- Emojis are used in logs for better visual identification of log types:
  - 🚀 - Starting processes
  - 📂 - Loading files
  - 🔍 - Fetching data
  - 📡 - API requests
  - 🔑 - Parameter details
  - 💾 - Saving data
  - ✅ - Success messages
  - ⚠️ - Warnings
  - ❌ - Errors
  - 📊 - Progress information
  - 🐞 - Debug information
  - ⏹️ - Process stopping

## Results

The script will save the results to the `results` directory in JSON files with the following naming convention:

`{platform}_{data_type}_{date}.json`

For example:
- `linkedin_profile_2025-06-24.json`
- `twitter_posts_2025-06-24.json`

### Result File Structure

Each result file contains:
- `date`: Current date (YYYY-MM-DD)
- `platform`: The social media platform name
- `data_type`: The type of data (profile, posts, etc.)
- `data`: The actual data retrieved from the API

Example:
```json
{
    "date": "2025-06-24",
    "platform": "linkedin",
    "data_type": "profile",
    "data": {
        // API response data
    }
}
```

## Customization

The tool uses a unified configuration file located at `config/config.json` that defines all API endpoints and parameters.

Example configuration structure:
```json
{
    "twitter_profile": {
        "api_url": "https://api-url-for-twitter-profile",
        "api_key": "your-api-key",
        "api_host": "api-host-for-twitter",
        "querystring": {
            "username": "your-twitter-username"
        }
    },
    "linkedin_posts": {
        "api_url": "https://api-url-for-linkedin-posts",
        "api_key": "your-api-key",
        "api_host": "api-host-for-linkedin",
        "querystring": {
            "username": "your-linkedin-username",
            "limit": "10"
        }
    }
    // Additional platforms and endpoints
}
```

Each key in the configuration represents a unique endpoint to process and should follow the pattern `{platform}_{data_type}`.

## Note

If a result file for the current date already exists, it will be deleted and overwritten with new data.

## Adding New Platforms

To add support for a new platform or endpoint:
1. Add a new entry in the `config.json` file with appropriate API settings
2. The new entry should follow the naming convention `{platform}_{data_type}`
3. Run the social API client to process all endpoints including your new one
