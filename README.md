# Social Media Data Retrieval Tool

This tool fetches data from various social media platforms (LinkedIn, Instagram, Twitter, Threads, Substack, and others) using APIs and saves the results to JSON files. It uses a unified API client approach to handle all platforms through a single configuration file.

## System Overview

The system consists of two main components:
1. **Social API Client**: Fetches data from social media platforms and stores it as JSON files
2. **Data Processor**: Transforms the fetched data into usable reports in CSV or Excel format

## Setup

1. Make sure you have Python installed
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
3. Configuration is stored in:
   - `config/config.json` - API endpoints and credentials
   - `config/mapping.json` - Field mapping for data processing

## Usage

### Step 1: Fetch Social Media Data

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

### Step 2: Process the Data

After collecting data, run the data processor:

```powershell
# Run the data processor
python process/data_processor.py
```

The processor will:
1. Ask if you want to enable debug mode (y/n)
2. Ask for your preferred output format (CSV or Excel)
3. Process all JSON files in the `results` directory according to the mappings
4. Generate output files in your chosen format

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

### Raw Data Files

The Social API Client saves the raw results to the `results` directory in JSON files with the following naming convention:

`{platform}_{data_type}_{date}.json`

For example:
- `linkedin_profile_2025-06-24.json`
- `twitter_posts_2025-06-24.json`

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

### Processed Data Files

The Data Processor outputs CSV or Excel files with processed data, naming them according to the platform and data type:

`{platform}_{data_type}.csv` or `{platform}_{data_type}.xlsx`

For example:
- `linkedin_profile.csv`
- `twitter_posts.xlsx`

## Data Processor Features

- Processes **all JSON files** in the `results` directory, regardless of their date.
- Extracts the date from each filename (e.g., `linkedin_posts_2025-06-25.json`) and includes it as the `file_date` field in the output.
- Supports mapping and transformation of fields as defined in `config/mapping.json`.
- Outputs one file per platform and data type, in either CSV or Excel format.
- Logging with configurable debug mode.

### Notes on Data Processing

- **All records in every JSON file are processed.** There is no filtering by internal date fields such as `posted`. The date used for each record is taken from the filename.
- The script requires Python 3 and the following packages:
  - `pandas`
  - `openpyxl` (if saving as Excel)
- Logging output is shown in the console.

## Configuration

### API Configuration

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

### Field Mapping Configuration

The processor uses a mapping configuration file (`config/mapping.json`) to define how fields are extracted from the source data. The configuration supports:

- Multiple platform and data type combinations
- Different data structures (single records or arrays)
- Custom field types (string, integer, boolean_exists, count)
- Required field validation

Example mapping for LinkedIn posts:

```json
"linkedin_posts": {
    "type": "array",
    "array_path": "data.data",
    "fields": {
        "post_url": {
            "path": "post_url",
            "type": "string",
            "required": true
        },
        "num_reactions": {
            "path": "num_reactions",
            "type": "integer",
            "required": true
        },
        // other fields...
    }
}
```

#### Field Types

- **string**: Default type, extracts text value
- **integer**: Converts value to integer
- **boolean_exists**: Checks if a key exists (returns true/false)
- **count**: Counts items in an array

## System Workflow

1. The Social API Client fetches data from configured social media platforms
2. Data is saved as JSON files in the results directory
3. The Data Processor reads these files, extracts relevant information according to the mapping configuration
4. Processed data is saved as CSV or Excel files for reporting and analysis

## Adding New Platforms

To add support for a new platform or endpoint:
1. Add a new entry in the `config.json` file with appropriate API settings
2. Add corresponding mapping rules in `mapping.json`
3. The new entry should follow the naming convention `{platform}_{data_type}`
4. Run the social API client to process all endpoints including your new one
5. Run the data processor to generate reports for the new data

## Project Structure

```
reporting/
├── config/
│   ├── config.json - API configuration
│   ├── mapping.json - Field mapping rules
│   ├── logger_config.py - Logging configuration
│   └── README.md - Configuration guide
├── social_client/
│   ├── social_api_client.py - Fetches data from APIs
│   └── README.md - Client documentation
├── process/
│   ├── data_processor.py - Processes the fetched data
│   └── README.md - Processor documentation
├── results/ - Contains output files
│   └── README.md - Results directory information
└── README.md - Main documentation (this file)
```

---

For any issues or questions, please refer to the code comments or contact the maintainer.
