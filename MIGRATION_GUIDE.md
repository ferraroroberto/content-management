# 🔄 Migration Guide: From Old Structure to New Structure

This guide helps you migrate from the old project structure (using `init.py`) to the new, improved structure.

## 🆕 **What's New in v2.0**

### **New Entry Points**
- 🚀 **`launch.py`** - Simple, user-friendly main launcher
- 🖥️ **`cli.main`** - Advanced CLI interface for automation
- 🔄 **`process/pipeline.py`** - Pipeline orchestrator (moved from root)

### **Improved Organization**
- **CLI Package** - Dedicated command-line interface
- **Better Naming** - Files now reflect their actual purpose
- **Logical Grouping** - Related functionality organized together

## 🔄 **Migration Steps**

### **Step 1: Update Your Scripts**

#### **Old Way (Still Works)**
```bash
# Run complete pipeline
python3 init.py

# Run with debug mode
python3 init.py --debug

# Skip specific steps
python3 init.py --skip-api --skip-processing
```

#### **New Way (Recommended)**
```bash
# Run complete pipeline
python3 launch.py

# Run with debug mode
python3 launch.py --debug

# Skip specific steps
python3 launch.py --skip-api --skip-processing
```

#### **Advanced CLI (For Automation)**
```bash
# Run complete pipeline
python3 -m cli.main

# Run only specific components
python3 -m cli.main --api-only
python3 -m cli.main --process-only
python3 -m cli.main --notion-only

# Advanced options
python3 -m cli.main --debug --date 20241201 --quiet
```

### **Step 2: Update Your Automation Scripts**

#### **Cron Jobs**
```bash
# Old way
0 2 * * * cd /path/to/project && python3 init.py --quiet

# New way
0 2 * * * cd /path/to/project && python3 -m cli.main --quiet
```

#### **Shell Scripts**
```bash
#!/bin/bash
# Old way
python3 init.py --debug --skip-notion

# New way
python3 launch.py --debug --skip-notion

# Or for automation
python3 -m cli.main --debug --skip-notion --quiet
```

#### **Docker Commands**
```dockerfile
# Old way
CMD ["python3", "init.py"]

# New way
CMD ["python3", "launch.py"]

# Or for automation
CMD ["python3", "-m", "cli.main"]
```

### **Step 3: Update Import Statements**

If you have custom scripts that import from the old structure:

#### **Old Imports**
```python
# Old way - importing from root
from init import run_pipeline, configure_logger

# Old way - running pipeline
run_pipeline(debug_mode=True)
```

#### **New Imports**
```python
# New way - importing from process domain
from process.pipeline import run_pipeline, configure_logger

# New way - running pipeline
run_pipeline(debug_mode=True)
```

## 📋 **Command Line Options Comparison**

### **Pipeline Control Options**

| Option | Old (`init.py`) | New (`launch.py`) | CLI (`cli.main`) |
|--------|----------------|-------------------|-------------------|
| Debug mode | `--debug` | `--debug` | `--debug` |
| Skip API | `--skip-api` | `--skip-api` | `--skip-api` |
| Skip processing | `--skip-processing` | `--skip-processing` | `--skip-processing` |
| Skip aggregation | `--skip-aggregation` | `--skip-aggregation` | `--skip-aggregation` |
| Skip consolidation | `--skip-consolidation` | `--skip-consolidation` | `--skip-consolidation` |
| Skip Notion | `--skip-notion` | `--skip-notion` | `--skip-notion` |
| Reference date | `--date` | `--date` | `--date` |

### **New CLI-Only Options**

| Option | Description | Available In |
|--------|-------------|--------------|
| `--pipeline-only` | Run complete pipeline | `cli.main` |
| `--api-only` | Run only API collection | `cli.main` |
| `--process-only` | Run only data processing | `cli.main` |
| `--notion-only` | Run only Notion sync | `cli.main` |
| `--verbose` | Enable verbose output | `cli.main` |
| `--quiet` | Suppress output | `cli.main` |
| `--log-file` | Log to file | `cli.main` |

## 🌍 **Environment Variables**

The new CLI interface supports environment variables for better automation:

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
```

## 🔧 **Backward Compatibility**

### **What Still Works**
- ✅ **`init.py`** - Still functional, but deprecated
- ✅ **All existing functionality** - No features removed
- ✅ **Same command line options** - All flags work the same
- ✅ **Same output and behavior** - Identical results

### **What's Deprecated**
- ⚠️ **`init.py`** - Will be removed in future versions
- ⚠️ **Direct imports from root** - Should use domain-specific imports

### **What's New**
- 🆕 **`launch.py`** - New main entry point
- 🆕 **`cli.main`** - Advanced CLI interface
- 🆕 **Environment variable support** - Better automation
- 🆕 **Configuration files** - Persistent settings

## 🚨 **Troubleshooting Migration Issues**

### **Common Issues**

#### **Import Errors**
```python
# Error: ModuleNotFoundError: No module named 'init'
from init import run_pipeline

# Solution: Update import path
from process.pipeline import run_pipeline
```

#### **File Not Found**
```bash
# Error: launch.py: No such file or directory
python3 launch.py

# Solution: Ensure you're in the project root directory
cd /path/to/project
python3 launch.py
```

#### **Permission Denied**
```bash
# Error: Permission denied: launch.py
python3 launch.py

# Solution: Make executable
chmod +x launch.py
python3 launch.py
```

### **Getting Help**

If you encounter issues during migration:

1. **Check the new README** - Updated with new structure
2. **Review CLI documentation** - `cli/README.md`
3. **Test with old method** - `init.py` still works
4. **Check file permissions** - Ensure scripts are executable

## 📚 **Additional Resources**

- [Main README](README.md) - Project overview and setup
- [CLI Documentation](cli/README.md) - Advanced CLI usage
- [Process Documentation](process/README.md) - Data processing details
- [Social Client Documentation](social_client/README.md) - API client usage
- [Notion Documentation](notion/README.md) - Notion integration

## 🎯 **Migration Checklist**

- [ ] **Update automation scripts** to use new entry points
- [ ] **Update cron jobs** to use new commands
- [ ] **Update Docker configurations** if applicable
- [ ] **Update import statements** in custom scripts
- [ ] **Test new entry points** with your workflows
- [ ] **Update documentation** for your team
- [ ] **Remove old references** to `init.py` (optional)

## 🚀 **Benefits After Migration**

- ✅ **Clearer project structure** - Know where everything is
- ✅ **Better user experience** - Intuitive entry points
- ✅ **Advanced automation** - Environment variables and CLI options
- ✅ **Easier maintenance** - Logical organization
- ✅ **Future-proof** - Ready for new features

---

**Need Help?** If you encounter any issues during migration, the old `init.py` file will continue to work, so you can always fall back to it while troubleshooting.