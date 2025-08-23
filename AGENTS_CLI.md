# 🖥️ CLI Development Reference

*Comprehensive guide for building robust command line interfaces with multiple dependencies and commands.*

See [AGENTS.md](AGENTS.md#tldr) for core standards and [AGENTS_STRUCTURE.md](AGENTS_STRUCTURE.md#reorg-checklist) for structure guidance.

## 🏗️ **Architecture Patterns** {#cli-architecture}

### **Modular Structure**
```
cli/
├── main.py              # Main entry point
├── commands/            # Command modules
│   ├── __init__.py     # Command registration
│   ├── process.py      # Processing commands
│   └── api.py          # API commands
├── config.py            # Configuration
└── utils/               # Utilities
    ├── formatters.py    # Output formatting
    └── validators.py    # Input validation
```

### **Project Entry Points (minimal)**
```
project_root/
├── cli/
│   ├── main.py
│   └── commands/
└── requirements.txt
```

### **Command Registration**
```python
# commands/__init__.py
COMMANDS = {}

def register_command(name: str, command_class):
    COMMANDS[name] = command_class

def get_command(name: str):
    return COMMANDS.get(name)

# Register commands
from .process import ProcessCommand
from .api import ApiCommand
register_command('process', ProcessCommand)
register_command('api', ApiCommand)
```

## 🚀 **Implementation**

### **Main Entry Point**
```python
# cli/main.py
#!/usr/bin/env python3
import sys
import argparse
import logging
from .commands import get_command, COMMANDS
from .config import CLIConfig

logger = logging.getLogger(__name__)

class CLI:
    def __init__(self):
        self.config = CLIConfig()
        self.parser = self._build_parser()
    
    def _build_parser(self):
        parser = argparse.ArgumentParser(description="Application CLI")
        parser.add_argument('--verbose', '-v', action='store_true')
        parser.add_argument('--debug', action='store_true', help='Enable debug mode')
        
        subparsers = parser.add_subparsers(dest='command')
        for cmd_name, cmd_class in COMMANDS.items():
            cmd_class.add_parser(subparsers)
        
        return parser
    
    def run(self, args=None):
        try:
            parsed_args = self.parser.parse_args(args)
            if not parsed_args.command:
                self.parser.print_help()
                return 1
            
            command_class = get_command(parsed_args.command)
            command = command_class(self.config, logger)
            return command.execute(parsed_args)
            
        except KeyboardInterrupt:
            logger.info("⏹️ Operation cancelled by user")
            return 130
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}")
            if parsed_args.debug:
                raise
            return 1

def main():
    cli = CLI()
    sys.exit(cli.run())
```

### **Base Command Class**
```python
# cli/commands/base.py
from abc import ABC, abstractmethod
import argparse
import logging

class BaseCommand(ABC):
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    @classmethod
    @abstractmethod
    def add_parser(cls, subparsers):
        pass
    
    @abstractmethod
    def execute(self, args):
        pass
    
    def validate_args(self, args):
        return True
```

### **Command Implementation**
```python
# cli/commands/process.py
import argparse
import logging
from pathlib import Path
from .base import BaseCommand

logger = logging.getLogger(__name__)

class ProcessCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers):
        parser = subparsers.add_parser('process', help='Process data files')
        parser.add_argument('--input', '-i', required=True, help='Input file')
        parser.add_argument('--output', '-o', required=True, help='Output file')
        parser.add_argument('--format', choices=['json', 'csv'], default='json')
        parser.add_argument('--batch-size', type=int, default=1000)
    
    def validate_args(self, args):
        if not Path(args.input).exists():
            self.logger.error(f"❌ Input file not found: {args.input}")
            return False
        return True
    
    def execute(self, args):
        if not self.validate_args(args):
            return 1
        
        try:
            self.logger.info(f"🔍 Processing {args.input} -> {args.output}")
            # Your processing logic here
            self.logger.info("✅ Processing completed successfully")
            return 0
        except Exception as e:
            self.logger.error(f"❌ Processing error: {e}")
            return 1
```

## ⚙️ **Configuration & Utilities**

### **Configuration Management**
Create a `CLIConfig` class that:
- Loads JSON configuration files from common locations
- Provides default values for all settings
- Supports dot-notation access (e.g., `config.get('logging.level')`)
- Handles missing config files gracefully

### **Output Formatting**
```python
# cli/utils/formatters.py
import json
from rich.console import Console
from rich.table import Table

class OutputFormatter:
    def __init__(self, colorize=True):
        self.console = Console(color_system="auto" if colorize else None)
    
    def format_json(self, data, indent=2):
        return json.dumps(data, indent=indent, default=str)
    
    def format_table(self, data, headers):
        table = Table()
        for header in headers:
            table.add_column(header)
        for row in data:
            table.add_row(*[str(row.get(header, '')) for header in headers])
        return table
    
    def print_success(self, message):
        self.console.print(f"✅ {message}", style="green")
    
    def print_error(self, message):
        self.console.print(f"❌ {message}", style="red")
```

## 🔧 **Integration Patterns**

### **Module Discovery**
```python
# cli/utils/module_discovery.py
import importlib
import inspect
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class ModuleDiscoverer:
    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.discovered_modules = {}
    
    def discover_modules(self, module_paths):
        for module_path in module_paths:
            full_path = self.project_root / module_path
            if full_path.exists():
                if full_path.is_file():
                    self._discover_file_module(full_path)
                elif full_path.is_dir():
                    self._discover_directory_modules(full_path)
        return self.discovered_modules
    
    def _discover_file_module(self, file_path):
        try:
            module_name = file_path.stem
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._analyze_module(module_name, module)
        except Exception as e:
            logger.warning(f"⚠️ Could not load {file_path}: {e}")
    
    def _analyze_module(self, module_name, module):
        for name, obj in inspect.getmembers(module):
            if (inspect.isfunction(obj) or inspect.isclass(obj)) and \
               (hasattr(obj, 'cli_help') or hasattr(obj, 'cli_args')):
                self.discovered_modules[f"{module_name}.{name}"] = obj
                logger.debug(f"🔍 Discovered CLI-compatible module: {module_name}.{name}")
```

### **Function Wrapper**
```python
# cli/commands/wrapper.py
import argparse
import logging
from .base import BaseCommand

logger = logging.getLogger(__name__)

class FunctionWrapperCommand(BaseCommand):
    def __init__(self, func, func_config):
        self.func = func
        self.func_config = func_config
    
    @classmethod
    def create_from_function(cls, func, config):
        class DynamicCommand(cls):
            @classmethod
            def add_parser(cls, subparsers):
                parser = subparsers.add_parser(
                    config.get('name', func.__name__),
                    help=config.get('help', func.__doc__)
                )
                
                import inspect
                sig = inspect.signature(func)
                for param_name, param in sig.parameters.items():
                    if param_name == 'self':
                        continue
                    
                    if param.default == param.empty:
                        parser.add_argument(f'--{param_name}', required=True)
                    else:
                        parser.add_argument(f'--{param_name}', default=param.default)
            
            def execute(self, args):
                try:
                    kwargs = {}
                    for param_name in inspect.signature(func).parameters:
                        if hasattr(args, param_name):
                            kwargs[param_name] = getattr(args, param_name)
                    
                    self.logger.info(f"🚀 Executing function: {func.__name__}")
                    result = self.func(**kwargs)
                    
                    if result:
                        self.logger.info("✅ Function executed successfully")
                        return 0
                    else:
                        self.logger.error("❌ Function execution failed")
                        return 1
                        
                except Exception as e:
                    self.logger.error(f"❌ Function execution error: {e}")
                    return 1
        
        return DynamicCommand


For project structure reorganization guidance, see [AGENTS_STRUCTURE.md](AGENTS_STRUCTURE.md#reorg-checklist).

### **Project Structure Best Practices**

#### **✅ Do's**
- **Domain separation**: Group related functionality in domain-specific folders
- **Clear naming**: Use descriptive names that reflect actual purpose
- **Entry point consolidation**: Create single main launcher in root
- **Documentation**: Maintain README files in each domain folder
- **Backward compatibility**: Maintain import paths where possible
- **Incremental migration**: Reorganize in phases to avoid breaking changes

#### **❌ Don'ts**
- **Mixed concerns**: Don't mix different domains in single files
- **Unclear naming**: Avoid generic names like `init.py` or `main.py`
- **Root clutter**: Don't leave orchestration files in root directory
- **Broken imports**: Don't move files without updating all references
- **Big bang changes**: Avoid reorganizing everything at once

#### **🔧 Project Structure Implementation Checklist**
- [ ] Analyze current project structure
- [ ] Identify logical domains and groupings
- [ ] Plan file relocations and renames
- [ ] Create new folder structure
- [ ] Move files to appropriate domains
- [ ] Update all import statements
- [ ] Create new entry points
- [ ] Update documentation
- [ ] Test all functionality
- [ ] Validate new structure

## 🧪 **Testing**

### **CLI Testing Framework**
```python
# tests/test_cli.py
import pytest
import logging
from unittest.mock import Mock, patch
from cli.main import CLI

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)

class TestCLI:
    def setup_method(self):
        self.cli = CLI()
    
    def test_help_output(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            self.cli.run(['--help'])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert 'Application CLI' in captured.out
    
    def test_unknown_command(self):
        result = self.cli.run(['unknown'])
        assert result == 1
    
    def test_command_execution(self):
        mock_command = Mock()
        mock_command.execute.return_value = 0
        
        with patch('cli.commands.get_command', return_value=Mock(return_value=mock_command)):
            result = self.cli.run(['test'])
            assert result == 0
            mock_command.execute.assert_called_once()
```

## 📚 **Best Practices**

### **✅ Do's**
- **Modular design**: Separate commands into individual modules
- **Consistent interface**: Use consistent argument patterns
- **Error handling**: Implement graceful error handling with logging
- **Configuration**: Use external configuration files
- **Documentation**: Provide comprehensive help and examples
- **Testing**: Include tests for CLI functionality

### **❌ Don'ts**
- **Monolithic commands**: Avoid putting all logic in one command
- **Hardcoded values**: Don't hardcode paths or settings
- **Poor error messages**: Avoid generic error messages
- **No validation**: Don't skip input validation
- **Inconsistent patterns**: Avoid different argument styles

### **🔧 Implementation Checklist**
- [ ] Create modular command structure
- [ ] Implement base command class with logging
- [ ] Add argument validation
- [ ] Include help text and examples
- [ ] Implement error handling with proper logging
- [ ] Create configuration management
- [ ] Add unit tests
- [ ] Document usage examples

## 🚀 **Quick Start Template**

```python
#!/usr/bin/env python3
import argparse
import sys
import logging

# Configure logging
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Your CLI Description")
    parser.add_argument('--input', '-i', required=True, help='Input file')
    parser.add_argument('--output', '-o', required=True, help='Output file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    try:
        logger.info(f"🔍 Processing {args.input} -> {args.output}")
        # Your logic here
        logger.info("✅ Success!")
        return 0
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return 1

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
```

---

**Remember**: Follow the project's logging standards with emojis and ensure all commands use proper logging instead of print statements.