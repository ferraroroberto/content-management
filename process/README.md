# Social Media Data Reporting System

This system consists of two main components:
1. **Social API Client**: Fetches data from social media platforms
2. **Data Processor**: Transforms the fetched data into usable reports

## Social API Client

The Social API Client fetches data from various social media platforms using their respective APIs.

### Key Features

- Configurable API endpoints via JSON configuration
- Supports multiple social platforms and data types
- Automatic date-stamped file storage
- Debug mode for detailed logging
- Progress tracking for multiple endpoint processing

### Usage

Run the client from the command line:

```bash
python social_api_client.py
```

You'll be prompted to enable/disable debug mode, which provides more detailed logging information.

### Configuration

The client uses a configuration file (`config/config.json`) to define API endpoints, credentials, and query parameters:

```json
{
  "platform_datatype": {
    "api_url": "https://api.example.com/endpoint",
    "api_key": "your-api-key",
    "api_host": "api.example.com",
    "querystring": {
      "param1": "value1",
      "param2": "value2"
    }
  }
}
```

## Data Processor

The data processor extracts information from JSON files containing social media data and creates consolidated reports.

### Key Features

- Processes multiple social media platforms and data types
- Creates separate DataFrames for each data type (posts, profiles, etc.)
- Filters data by date (defaults to current date)
- Handles both single records (like profiles) and arrays of records (like posts)
- Configurable field mapping via JSON configuration
- Extracts video content and other special field types
- Orders columns consistently in output files

### Usage

Run the processor from the command line:

```bash
python data_processor.py
```

You'll be prompted to:
1. Enable/disable debug mode
2. Choose output format (Excel/CSV)

### Configuration

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

### Field Types

- **string**: Default type, extracts text value
- **integer**: Converts value to integer
- **boolean_exists**: Checks if a key exists (returns true/false)
- **count**: Counts items in an array

### Date Filtering

The processor extracts dates from filenames (format: YYYY-MM-DD) and filters data to include only records from the current date.

For array data types (like posts), it additionally filters based on the "posted" field.

## System Workflow

1. The Social API Client fetches data from configured social media platforms
2. Data is saved as JSON files in the results directory
3. The Data Processor reads these files, extracts relevant information according to the mapping configuration
4. Processed data is saved as CSV or Excel files for reporting and analysis

## Output

The processed data is saved to the results directory with the data type and timestamp in the filename.

