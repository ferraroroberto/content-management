# 🖥️ CLI Package

The Command Line Interface package provides a comprehensive interface for the Social Media Automation Suite, offering both simple and advanced usage options.

## 🚀 Quick Start

### Basic Usage

```bash
# Run complete pipeline
python3 -m cli.main

# Run only API collection
python3 -m cli.main --api-only

# Run only data processing
python3 -m cli.main --process-only

# Run only Notion sync
python3 -m cli.main --notion-only
```

### Advanced Usage

```bash
# Enable debug mode
python3 -m cli.main --debug

# Skip specific steps
python3 -m cli.main --skip-api --skip-processing

# Specify processing date
python3 -m cli.main --date 20241201

# Quiet mode (minimal output)
python3 -m cli.main --quiet

# Verbose mode
python3 -m cli.main --verbose
```

## 📋 Command Line Options

### Execution Modes

| Option | Description |
|--------|-------------|
| `--pipeline-only` | Run complete pipeline (default) |
| `--api-only` | Run only social media API collection |
| `--process-only` | Run only data processing steps |
| `--notion-only` | Run only Notion synchronization |

### Pipeline Control

| Option | Short | Description |
|--------|-------|-------------|
| `--debug` | `-d` | Enable debug mode |
| `--skip-api` | `-s` | Skip API data collection |
| `--skip-processing` | `-p` | Skip data processing |
| `--skip-aggregation` | `-a` | Skip profile aggregation |
| `--skip-consolidation` | `-c` | Skip posts consolidation |
| `--skip-notion` | `-n` | Skip Notion update |
| `--date` | | Reference date (YYYYMMDD format) |

### Output Control

| Option | Short | Description |
|--------|-------|-------------|
| `--verbose` | `-v` | Enable verbose output |
| `--quiet` | | Suppress all output except errors |
| `--log-file` | | Log to specified file instead of console |

## 🌍 Environment Variables

The CLI supports configuration through environment variables, which can be useful for automation and CI/CD:

```bash
# Enable debug mode
export DEBUG=1

# Skip specific steps
export SKIP_API=1
export SKIP_PROCESSING=1

# Set processing date
export REFERENCE_DATE=20241201

# Configure logging
export LOG_LEVEL=DEBUG
export LOG_FILE=/path/to/logfile.log

# Set timeouts and retries
export TIMEOUT=600
export RETRY_ATTEMPTS=5
export RETRY_DELAY=10
```

### Available Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Enable debug mode | `false` |
| `VERBOSE` | Enable verbose output | `false` |
| `QUIET` | Suppress output | `false` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FILE` | Log file path | `None` |
| `SKIP_API` | Skip API collection | `false` |
| `SKIP_PROCESSING` | Skip data processing | `false` |
| `SKIP_AGGREGATION` | Skip profile aggregation | `false` |
| `SKIP_CONSOLIDATION` | Skip posts consolidation | `false` |
| `SKIP_NOTION` | Skip Notion sync | `false` |
| `REFERENCE_DATE` | Processing date | `None` |
| `TIMEOUT` | Operation timeout (seconds) | `300` |
| `RETRY_ATTEMPTS` | Number of retry attempts | `3` |
| `RETRY_DELAY` | Delay between retries (seconds) | `5` |

## ⚙️ Configuration Files

The CLI can load configuration from JSON files. Configuration files are searched in this order:

1. `~/.social_automation/cli_config.json` (user home directory)
2. `./cli_config.json` (current working directory)
3. `cli/cli_config.json` (package directory)

### Configuration File Format

```json
{
  "debug": false,
  "verbose": false,
  "quiet": false,
  "log_level": "INFO",
  "log_file": null,
  "default_skip_api": false,
  "default_skip_processing": false,
  "default_skip_aggregation": false,
  "default_skip_consolidation": false,
  "default_skip_notion": false,
  "default_reference_date": null,
  "timeout": 300,
  "retry_attempts": 3,
  "retry_delay": 5
}
```

## 🔧 Programmatic Usage

The CLI package can also be used programmatically:

```python
from cli.main import main
from cli.config import get_config

# Get configuration
config = get_config()
config.set('debug', True)

# Run CLI programmatically
import sys
sys.argv = ['cli.main', '--api-only']
main()
```

## 📁 Package Structure

```
cli/
├── __init__.py          # Package initialization
├── main.py              # Main CLI interface
├── config.py            # Configuration management
└── README.md            # This file
```

## 🚨 Error Handling

The CLI provides comprehensive error handling:

- **Graceful fallbacks** for missing modules
- **Clear error messages** with actionable information
- **Logging for debugging** reorganization issues
- **Exit codes** for automation integration

### Exit Codes

| Code | Description |
|------|-------------|
| `0` | Success |
| `1` | General error |
| `2` | Configuration error |
| `3` | Module execution error |
| `4` | Import error |

## 🔄 Integration with Main Launcher

The CLI package works alongside the main `launch.py` file:

- **`launch.py`** - Simple, user-friendly interface
- **`cli.main`** - Advanced, automation-friendly interface

Both use the same underlying pipeline logic from `process.pipeline`.

## 📚 Examples

### Basic Automation Script

```bash
#!/bin/bash
# Run social media automation daily

# Set environment variables
export DEBUG=0
export QUIET=1
export LOG_FILE=/var/log/social_automation.log

# Run the pipeline
python3 -m cli.main --date $(date +%Y%m%d)

# Check exit code
if [ $? -eq 0 ]; then
    echo "✅ Automation completed successfully"
else
    echo "❌ Automation failed"
    exit 1
fi
```

### Cron Job Example

```bash
# Add to crontab to run daily at 2 AM
0 2 * * * cd /path/to/project && python3 -m cli.main --quiet --log-file /var/log/social_automation.log
```

### Docker Integration

```dockerfile
# Dockerfile example
FROM python:3.9-slim

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

# Set default environment variables
ENV QUIET=1
ENV LOG_LEVEL=INFO

# Run CLI as default command
CMD ["python3", "-m", "cli.main"]
```

## 🤝 Contributing

When contributing to the CLI package:

1. **Maintain backward compatibility** with existing scripts
2. **Add comprehensive help text** for new options
3. **Include environment variable support** for new features
4. **Update this README** with new functionality
5. **Add tests** for new CLI features

## 📖 Related Documentation

- [Main README](../README.md) - Project overview and setup
- [Process README](../process/README.md) - Data processing documentation
- [Social Client README](../social_client/README.md) - API client documentation
- [Notion README](../notion/README.md) - Notion integration documentation