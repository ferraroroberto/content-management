"""
⚙️ CLI Configuration

This module provides configuration options for the CLI interface,
including default settings, environment variable mappings, and
configuration file handling.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

class CLIConfig:
    """Configuration class for CLI operations."""
    
    def __init__(self):
        self.config_file = self._find_config_file()
        self.settings = self._load_default_settings()
        self._load_config_file()
        self._load_environment_overrides()
    
    def _find_config_file(self) -> Optional[Path]:
        """Find the CLI configuration file."""
        # Look for config in order of preference
        config_locations = [
            Path.home() / '.social_automation' / 'cli_config.json',
            Path.cwd() / 'cli_config.json',
            Path(__file__).parent / 'cli_config.json',
        ]
        
        for location in config_locations:
            if location.exists():
                return location
        
        return None
    
    def _load_default_settings(self) -> Dict[str, Any]:
        """Load default CLI settings."""
        return {
            'debug': False,
            'verbose': False,
            'quiet': False,
            'log_level': 'INFO',
            'log_file': None,
            'default_skip_api': False,
            'default_skip_processing': False,
            'default_skip_aggregation': False,
            'default_skip_consolidation': False,
            'default_skip_notion': False,
            'default_reference_date': None,
            'timeout': 300,  # 5 minutes
            'retry_attempts': 3,
            'retry_delay': 5,  # seconds
        }
    
    def _load_config_file(self):
        """Load configuration from file if it exists."""
        if not self.config_file or not self.config_file.exists():
            return
        
        try:
            import json
            with open(self.config_file, 'r') as f:
                file_settings = json.load(f)
                self.settings.update(file_settings)
        except Exception as e:
            # Log error but continue with defaults
            print(f"Warning: Could not load config file {self.config_file}: {e}")
    
    def _load_environment_overrides(self):
        """Load configuration overrides from environment variables."""
        env_mappings = {
            'DEBUG': 'debug',
            'VERBOSE': 'verbose',
            'QUIET': 'quiet',
            'LOG_LEVEL': 'log_level',
            'LOG_FILE': 'log_file',
            'SKIP_API': 'default_skip_api',
            'SKIP_PROCESSING': 'default_skip_processing',
            'SKIP_AGGREGATION': 'default_skip_aggregation',
            'SKIP_CONSOLIDATION': 'default_skip_consolidation',
            'SKIP_NOTION': 'default_skip_notion',
            'REFERENCE_DATE': 'default_reference_date',
            'TIMEOUT': 'timeout',
            'RETRY_ATTEMPTS': 'retry_attempts',
            'RETRY_DELAY': 'retry_delay',
        }
        
        for env_var, setting_key in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                # Convert string values to appropriate types
                if setting_key in ['debug', 'verbose', 'quiet']:
                    self.settings[setting_key] = env_value.lower() in ['1', 'true', 'yes', 'on']
                elif setting_key in ['timeout', 'retry_attempts', 'retry_delay']:
                    try:
                        self.settings[setting_key] = int(env_value)
                    except ValueError:
                        pass  # Keep default if conversion fails
                else:
                    self.settings[setting_key] = env_value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self.settings.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set a configuration value."""
        self.settings[key] = value
    
    def save_config(self, file_path: Optional[Path] = None):
        """Save current configuration to file."""
        if file_path is None:
            file_path = self.config_file or Path.home() / '.social_automation' / 'cli_config.json'
        
        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            import json
            with open(file_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
            self.config_file = file_path
        except Exception as e:
            raise RuntimeError(f"Could not save config to {file_path}: {e}")
    
    def get_environment_help(self) -> str:
        """Get help text for environment variables."""
        return """
Environment Variables:
    DEBUG                   Set to '1' to enable debug mode
    VERBOSE                Set to '1' to enable verbose output
    QUIET                  Set to '1' to suppress output
    LOG_LEVEL              Set logging level (DEBUG, INFO, WARNING, ERROR)
    LOG_FILE               Set log file path
    SKIP_API               Set to '1' to skip API collection by default
    SKIP_PROCESSING        Set to '1' to skip data processing by default
    SKIP_AGGREGATION       Set to '1' to skip profile aggregation by default
    SKIP_CONSOLIDATION     Set to '1' to skip posts consolidation by default
    SKIP_NOTION            Set to '1' to skip Notion sync by default
    REFERENCE_DATE         Set default reference date (YYYYMMDD format)
    TIMEOUT                Set operation timeout in seconds
    RETRY_ATTEMPTS         Set number of retry attempts
    RETRY_DELAY            Set delay between retries in seconds
        """.strip()

# Global CLI configuration instance
cli_config = CLIConfig()

def get_config() -> CLIConfig:
    """Get the global CLI configuration instance."""
    return cli_config