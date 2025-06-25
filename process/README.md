# Social Media Data Processor

This module processes social media data files and generates consolidated reports.

## Overview

The data processor extracts information from JSON files containing social media data (posts, profiles, etc.) and creates separate DataFrames for each data type, which are then saved as CSV or Excel files. It uses a configurable mapping system to handle different data structures and formats.

## Key Features

- Processes multiple social media platforms and data types
- Creates separate DataFrames for each data type (posts, profiles, etc.)
- Filters data by date (defaults to current date)
- Handles both single records (like profiles) and arrays of records (like posts)
- Configurable field mapping via JSON configuration
- Extracts video content and other special field types
- Orders columns consistently in output files

## Usage

Run the processor from the command line:

```bash
python data_processor.py
```

You'll be prompted to:
1. Enable/disable debug mode
2. Choose output format (Excel/CSV)

## Configuration

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

## Field Types

- **string**: Default type, extracts text value
- **integer**: Converts value to integer
- **boolean_exists**: Checks if a key exists (returns true/false)
- **count**: Counts items in an array

## Date Filtering

The processor extracts dates from filenames (format: YYYY-MM-DD) and filters data to include only records from the current date.

For array data types (like posts), it additionally filters based on the "posted" field.

## Output

The processed data is saved to the results directory with the data type and timestamp in the filename:

