# Social API Client

A Python utility for fetching and storing data from various social media platform APIs.

## Overview

This tool automates the process of retrieving data from social media platforms through their APIs. It handles authentication, data fetching, and storage in a structured format for further analysis.

## Features

- Supports multiple social media platforms in a configurable way
- Saves API responses as JSON files with metadata
- Includes debug mode for development and troubleshooting
- Handles API errors gracefully
- Tracks progress during execution

## Installation

### Prerequisites

- Python 3.6+
- Required packages: `requests`

### Setup

1. Clone the repository or download the source code
2. Install dependencies:
   ```bash
   pip install requests
   ```

## Configuration

Create a `config.json` file in the `config` directory with the following structure:

```json
{
  "platform_endpoint": {
    "api_url": "https://api.example.com/endpoint",
    "api_key": "your_api_key",
    "api_host": "api.example.com",
    "querystring": {
      "param1": "value1",
      "param2": "value2"
    }
  }
}
```

Replace `platform_endpoint` with your specific platform and endpoint names (e.g., `twitter_tweets`, `instagram_user_data`).

## Usage

Run the script from the command line:

```bash
python social_api_client.py
```

You'll be prompted to enable debug mode. Type 'y' for debug mode or 'n' for normal operation.

In debug mode:
- More detailed logs will be displayed
- You'll be prompted to continue after each endpoint

## Output

Results are saved in the `results` directory with filenames following this pattern:
```
platform_datatype_YYYY-MM-DD.json
```

Each file contains:
- Date of retrieval
- Platform name
- Data type
- The API response data

## Project Structure

```
reporting/
├── config/
│   ├── config.json
│   └── logger_config.py
├── social_client/
│   ├── social_api_client.py
│   └── README.md
└── results/
    └── platform_datatype_YYYY-MM-DD.json
```

## Error Handling

The client handles and logs various errors:
- Configuration file not found
- Invalid JSON in configuration
- API request failures
- Missing platform configurations

## License

[Your License Here]

## Contributing

[Your Contributing Guidelines Here]
