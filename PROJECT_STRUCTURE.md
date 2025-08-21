# 🏗️ Project Structure Documentation

This document provides a comprehensive overview of the Social Media Automation Suite's project structure, explaining the organization, purpose, and relationships between different components.

## 🎯 **Project Overview**

The Social Media Automation Suite is organized around **domain-driven design principles**, with clear separation of concerns and logical grouping of related functionality. The structure follows modern software engineering best practices for maintainability and scalability.

## 📁 **Root Directory Structure**

```
social-media-automation-suite/
├── 🚀 launch.py                    # Main application launcher
├── 🖥️ cli/                         # Command Line Interface package
├── 📡 social_client/               # Social Media API clients
├── 🔄 process/                     # Data processing & database operations
├── 📘 notion/                      # Notion integration
├── ⚙️ config/                      # Configuration & settings
├── 📚 docs/                        # Additional documentation
├── 📦 requirements.txt             # Python dependencies
├── 🔄 MIGRATION_GUIDE.md          # Migration guide from old structure
├── 📖 README.md                    # Main project documentation
└── 🏗️ PROJECT_STRUCTURE.md         # This file
```

## 🚀 **Entry Points & Launchers**

### **Primary Entry Point: `launch.py`**
- **Purpose**: Simple, user-friendly main launcher
- **Target Users**: End users, developers, manual execution
- **Features**: 
  - Complete pipeline execution
  - Step-by-step control
  - Debug mode support
  - Date specification

### **Advanced Entry Point: `cli.main`**
- **Purpose**: Advanced CLI interface for automation
- **Target Users**: DevOps, automation scripts, CI/CD
- **Features**:
  - Component-specific execution
  - Environment variable support
  - Configuration file support
  - Advanced logging options

### **Legacy Entry Point: `init.py`**
- **Purpose**: Backward compatibility (deprecated)
- **Status**: Will be removed in future versions
- **Recommendation**: Migrate to `launch.py` or `cli.main`

## 📡 **Social Client Domain (`social_client/`)**

### **Purpose**
Handles all interactions with external social media APIs, providing a unified interface for data collection across multiple platforms.

### **Components**
- **`social_api_client.py`**: Multi-platform API client
- **`README.md`**: API client documentation

### **Responsibilities**
- API authentication and management
- Rate limiting and error handling
- Data collection from multiple platforms
- Response formatting and validation

### **Supported Platforms**
- LinkedIn (Profile & Posts)
- Instagram (Profile & Posts)
- Twitter/X (Profile & Posts)
- Threads (Profile & Posts)
- Substack (Profile & Posts)

## 🔄 **Process Domain (`process/`)**

### **Purpose**
Core data processing engine that transforms raw API data into structured database records, handles aggregations, and manages database operations.

### **Components**
- **`pipeline.py`**: Pipeline orchestrator (moved from root)
- **`data_processor.py`**: Data transformation engine
- **`supabase_uploader.py`**: Database upload operations
- **`profile_aggregator.py`**: Profile data aggregation
- **`posts_consolidator.py`**: Posts data consolidation
- **`README.md`**: Processing documentation

### **Responsibilities**
- Data transformation and normalization
- Database schema management
- Batch processing and optimization
- Data aggregation and consolidation
- Pipeline orchestration and execution

### **Key Features**
- Field mapping based on configuration
- Automatic type conversion
- Database table creation
- Batch processing for large datasets
- Comprehensive error handling

## 📘 **Notion Domain (`notion/`)**

### **Purpose**
Manages all Notion-related operations, including database synchronization, structure management, and data updates.

### **Components**
- **`notion_update.py`**: Notion database updates
- **`notion_supabase_sync.py`**: Notion-Supabase synchronization
- **`notion_database_structure.py`**: Database structure management
- **`README.md`**: Notion integration documentation

### **Responsibilities**
- Notion API integration
- Database structure management
- Data synchronization with Supabase
- Relationship mapping and maintenance

## ⚙️ **Configuration Domain (`config/`)**

### **Purpose**
Centralized configuration management for all system components, including API keys, database settings, and operational parameters.

### **Components**
- **`config_example.json`**: Example configuration template
- **`mapping.json`**: Field mapping definitions
- **`logger_config.py`**: Logging configuration
- **`README.md`**: Configuration documentation

### **Responsibilities**
- Environment-specific configuration
- API key management
- Database connection settings
- Logging configuration
- Field mapping definitions

## 🖥️ **CLI Package (`cli/`)**

### **Purpose**
Advanced command-line interface providing automation-friendly features, environment variable support, and comprehensive configuration options.

### **Components**
- **`__init__.py`**: Package initialization
- **`main.py`**: Main CLI interface
- **`config.py`**: Configuration management
- **`README.md`**: CLI documentation

### **Features**
- Component-specific execution modes
- Environment variable overrides
- Configuration file support
- Advanced logging options
- Automation-friendly output

### **Execution Modes**
- **`--pipeline-only`**: Complete pipeline execution
- **`--api-only`**: API collection only
- **`--process-only`**: Data processing only
- **`--notion-only`**: Notion sync only

## 📚 **Documentation Domain (`docs/`)**

### **Purpose**
Comprehensive documentation covering data structures, database schemas, and technical specifications.

### **Components**
- **`DATA_STRUCTURE_DOCUMENTATION.md`**: Data structure specifications
- **`SUPABASE_SCHEMA.md`**: Database schema documentation

## 🔄 **Data Flow Architecture**

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Social APIs    │────▶│ Data Processing │────▶│    Supabase     │
│   (RapidAPI)    │     │   & Transform   │     │   PostgreSQL    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                        │                        │
         │                        │                        ▼
         │                        │              ┌─────────────────┐
         │                        │              │     Notion      │
         │                        │              │   Databases     │
         │                        │              └─────────────────┘
         ▼                        ▼
┌─────────────────┐     ┌─────────────────┐
│   Raw Data      │     │ Processed Data  │
│   Collection    │     │   Aggregation   │
└─────────────────┘     └─────────────────┘
```

## 🎯 **Design Principles**

### **1. Domain-Driven Design**
- **Clear boundaries** between different functional areas
- **Logical grouping** of related functionality
- **Minimal coupling** between domains

### **2. Single Responsibility Principle**
- **Each module** has one clear purpose
- **Clear interfaces** between components
- **Focused functionality** within each domain

### **3. Separation of Concerns**
- **Data collection** separated from processing
- **Configuration** separated from business logic
- **CLI interface** separated from core functionality

### **4. Maintainability**
- **Consistent naming** conventions
- **Clear documentation** for each component
- **Logical file organization**

## 🔧 **Development Workflow**

### **Adding New Features**
1. **Identify the domain** for the new feature
2. **Create or update** files within the appropriate domain
3. **Update documentation** in the domain's README
4. **Add CLI options** if needed in `cli/main.py`
5. **Update main launcher** if needed in `launch.py`

### **Modifying Existing Features**
1. **Locate the domain** containing the feature
2. **Make changes** within the domain
3. **Update domain documentation** if needed
4. **Test integration** with other domains

### **Adding New Domains**
1. **Create domain directory** with clear purpose
2. **Add `__init__.py`** for package initialization
3. **Create `README.md`** documenting the domain
4. **Update main launcher** to include new domain
5. **Add CLI options** if needed

## 📋 **File Naming Conventions**

### **Python Files**
- **Use snake_case** for file names
- **Descriptive names** that indicate purpose
- **Consistent suffixes** for similar functionality

### **Documentation Files**
- **Use UPPER_CASE** for README files
- **Descriptive names** for technical documents
- **Markdown format** for all documentation

### **Configuration Files**
- **Use descriptive names** indicating purpose
- **JSON format** for structured configuration
- **Example files** with `_example` suffix

## 🚨 **Important Considerations**

### **Import Paths**
- **Use relative imports** within domains
- **Use absolute imports** for cross-domain dependencies
- **Maintain `sys.path`** modifications where needed

### **Dependencies**
- **Minimize cross-domain dependencies**
- **Use interfaces** for domain communication
- **Document dependencies** clearly

### **Configuration**
- **Centralize configuration** in the config domain
- **Use environment variables** for sensitive data
- **Provide sensible defaults** for all settings

## 🔄 **Migration and Evolution**

### **Version Compatibility**
- **Maintain backward compatibility** during transitions
- **Provide migration guides** for structural changes
- **Deprecate old patterns** gradually

### **Future Enhancements**
- **Plan for scalability** in domain design
- **Consider microservice architecture** for complex domains
- **Maintain clear interfaces** for external integrations

## 📖 **Related Documentation**

- [Main README](README.md) - Project overview and setup
- [Migration Guide](MIGRATION_GUIDE.md) - Transition from old structure
- [CLI Documentation](cli/README.md) - Advanced CLI usage
- [Process Documentation](process/README.md) - Data processing details
- [Social Client Documentation](social_client/README.md) - API client usage
- [Notion Documentation](notion/README.md) - Notion integration
- [Configuration Documentation](config/README.md) - Configuration management

---

**Last Updated**: December 2024  
**Version**: 2.0  
**Maintainer**: Social Media Automation Suite Team