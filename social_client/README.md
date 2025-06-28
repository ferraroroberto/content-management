# Social API Client

A Python-based API client for fetching data from various social media platforms through their APIs. This tool automates the collection of social media data and saves it in JSON format for further analysis and reporting.

## Features

- 🚀 Fetch data from multiple social media platforms
- 💾 Automatic saving of results in JSON format with timestamps
- 🔄 Skip existing data to avoid duplicate API calls
- 🐞 Debug mode for detailed logging and step-by-step execution
- 📊 Progress tracking for batch processing
- 🎯 Target specific platforms or process all at once
- 📝 Comprehensive logging with emoji indicators

## Prerequisites

- Python 3.x
- Required Python packages:
  - `requests`
  - `argparse`
  - Additional dependencies from parent modules

## Configuration

The client reads configuration from `../config/config.json`. The configuration file should contain:

- API endpoints for each platform
- API keys and hosts
- Query parameters
- Results folder path

Example configuration structure:
```json
{
  "platform_name": {
    "api_url": "https://api.example.com/endpoint",
    "api_key": "your-api-key",
    "api_host": "api.example.com",
    "querystring": {
      "param1": "value1"
    }
  },
  "folder_results_raw": "results/raw"
}
```

## Usage

### Command Line Arguments

```bash
python social_api_client.py [options]
```

#### Options:

- `--debug`: Enable debug mode with verbose logging
- `--skip-existing`: Skip platforms with today's data already collected (default: enabled)
- `--no-skip`: Force re-fetching of data even if today's file exists
- `--platform PLATFORM_NAME`: Process only a specific platform

### Examples

1. **Process all platforms (skip existing)**:
   ```bash
   python social_api_client.py
   ```

2. **Process all platforms in debug mode**:
   ```bash
   python social_api_client.py --debug
   ```

3. **Process specific platform**:
   ```bash
   python social_api_client.py --platform twitter_analytics
   ```

4. **Force re-fetch all data**:
   ```bash
   python social_api_client.py --no-skip
   ```

5. **Debug mode with specific platform**:
   ```bash
   python social_api_client.py --debug --platform instagram_insights
   ```

## Output

Results are saved in the configured results directory (default: `results/raw/`) with the following naming convention:

```
{platform}_{data_type}_{YYYY-MM-DD}.json
```

Each JSON file contains:
- `date`: Collection date
- `platform`: Platform name
- `data_type`: Type of data collected
- `data`: The actual API response data

## Logging

The client uses a custom logger configuration with:
- Console output with emoji indicators
- Debug/Info level switching
- Clear status messages for each operation

### Log Indicators:
- 🚀 Starting/Processing
- ✅ Success
- ❌ Error
- ⚠️ Warning
- 📂 File operations
- 🔄 Skipping existing
- 📊 Progress updates
- 🐞 Debug mode

## Error Handling

The client handles various error scenarios:
- Missing configuration files
- Invalid JSON in config
- API request failures
- Network timeouts
- File system errors

## Development

To extend the client with new platforms:

1. Add platform configuration to `config.json`
2. Ensure the configuration includes all required fields:
   - `api_url`
   - `api_key`
   - `api_host`
   - `querystring` (optional)

The client will automatically detect and process new platforms added to the configuration.

## Notes

- The client checks for existing files to prevent duplicate API calls on the same day
- Use `--no-skip` carefully to avoid unnecessary API usage
- Debug mode includes prompts between platform processing for controlled execution
- All timestamps are in local timezone
