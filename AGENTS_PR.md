# Pull Request Template

## 🎯 Purpose
Clear explanation of what this PR accomplishes and why it's needed.

## 🔄 Changes Made
- List of specific changes, files modified, and new features added
- Reference to any related issues or requirements

## 🧪 Testing Instructions

### **Prerequisites**
- Python 3.8+ installed
- Git installed
- Access to the repository

### **Environment Setup Commands** {#environment-setup}

See [AGENTS.md](AGENTS.md#virtual-environment-policy) for the authoritative policy. Use the `.venv` interpreter directly (no activation).

**Windows PowerShell:**
```powershell
# 1. Clone repo and checkout specific branch into new folder
git clone -b [branch-name] [repo-url] [folder-name]

# 2. Navigate to the folder
cd [folder-name]

# 3. Create virtual environment
python -m venv .venv

# 4. Install requirements with venv interpreter
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Unix/Linux/macOS:**
```bash
# 1. Clone repo and checkout specific branch into new folder
git clone -b [branch-name] [repo-url] [folder-name]

# 2. Navigate to the folder
cd [folder-name]

# 3. Create virtual environment
python3 -m venv .venv

# 4. Install requirements with venv interpreter
./.venv/bin/python -m pip install -r requirements.txt
```

### **Test Execution**
1. Run the main script: `python main.py` (or your specific entry point)
2. Verify output matches expected results
3. Check logs for any errors or warnings
4. Test any new functionality added

## 📋 Requirements for Testing
- **Minimal dependencies**: List only the packages needed for this PR
- **Environment setup**: Commands above to create clean test environment
- **Data requirements**: Any sample data or configuration needed

## 🚨 Breaking Changes
Document any changes that might affect existing functionality.

## 📚 Additional Notes
Any other information reviewers should know.

---

## 📝 Instructions for Contributors

### **When Creating a Pull Request:**

1. **Copy this template** and rename it to match your PR
2. **Fill in all sections** with relevant information
3. **Replace placeholders** like `[branch-name]`, `[repo-url]`, `[folder-name]` with actual values
4. **Test the commands** yourself before submitting
5. **Ensure requirements.txt** contains only the minimal dependencies needed

### **Example Completed Section:**

**Windows PowerShell:**
```powershell
git clone -b feature/notion-rules https://github.com/ferraroroberto/automation.git test-notion-rules
cd test-notion-rules
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Unix/Linux/macOS:**
```bash
git clone -b feature/notion-rules https://github.com/ferraroroberto/automation.git test-notion-rules
cd test-notion-rules
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

### **Quality Checklist:**
- [ ] Commands tested and verified working
- [ ] Requirements.txt contains minimal dependencies only
- [ ] All placeholders replaced with actual values
- [ ] Test instructions are clear and complete
- [ ] Breaking changes documented if any
