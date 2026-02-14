# INSTALLATION INSTRUCTIONS

## Quick Install for Windows (F:\ANTI\memopad)

### Method 1: Automatic Installation (Recommended)

1. **Download all files** from this package to a temporary folder

2. **Double-click `install.bat`** (or right-click → Run as Administrator)

3. **Restart Claude Desktop**

4. **Done!** Test by asking Claude: "Create a note titled 'Test' with content 'Hello World'"

### Method 2: PowerShell Installation

1. **Open PowerShell** (right-click Start → Windows PowerShell)

2. **Navigate to the downloaded files:**
   ```powershell
   cd path\to\downloaded\files
   ```

3. **Run the installer:**
   ```powershell
   .\install_memopad.ps1
   ```

4. **Restart Claude Desktop**

### Method 3: Manual Installation

1. **Create the directory:**
   ```powershell
   New-Item -ItemType Directory -Path "F:\ANTI\memopad" -Force
   ```

2. **Copy the main server file:**
   ```powershell
   Copy-Item memopad_server_fixed.py F:\ANTI\memopad\server.py
   ```

3. **Copy other files:**
   ```powershell
   Copy-Item test_memopad.py F:\ANTI\memopad\
   Copy-Item QUICKSTART.md F:\ANTI\memopad\
   Copy-Item README.md F:\ANTI\memopad\
   ```

4. **Configure Claude Desktop:**
   
   Create/edit: `%APPDATA%\Claude\claude_desktop_config.json`
   
   ```json
   {
     "mcpServers": {
       "memopad": {
         "command": "python",
         "args": [
           "F:\\ANTI\\memopad\\server.py"
         ]
       }
     }
   }
   ```

5. **Restart Claude Desktop**

---

## Verification

### Quick Test

After installation, run:
```powershell
cd F:\ANTI\memopad
python verify_installation.py
```

This will check:
- ✓ Python version
- ✓ Installation directory
- ✓ Server file validity
- ✓ Storage directory
- ✓ Claude Desktop configuration
- ✓ Server functionality

### Full Test Suite

```powershell
cd F:\ANTI\memopad
python test_memopad.py
```

Expected output:
```
============================================================
Tests Run: 20
Passed: 20 ✓
Failed: 0 ✗
Success Rate: 100.0%
============================================================
```

---

## File Overview

| File | Purpose |
|------|---------|
| `memopad_server_fixed.py` | Main server (renamed to `server.py`) |
| `install.bat` | Windows batch installer |
| `install_memopad.ps1` | PowerShell installer |
| `verify_installation.py` | Installation verification tool |
| `test_memopad.py` | Complete test suite |
| `README.md` | Full documentation |
| `QUICKSTART.md` | Quick start guide |
| `MEMOPAD_ANALYSIS.md` | Technical analysis & fixes |
| `package.json` | Package metadata |

---

## Directory Structure After Installation

```
F:\ANTI\memopad\
├── server.py              # Main MCP server
├── test_memopad.py        # Test suite
├── verify_installation.py # Verification script
├── README.md              # Full documentation
├── QUICKSTART.md          # Quick start guide
└── MEMOPAD_ANALYSIS.md    # Technical details

%USERPROFILE%\.memopad\
└── notes.json             # Your notes (created automatically)

%APPDATA%\Claude\
└── claude_desktop_config.json  # Claude Desktop configuration
```

---

## Troubleshooting

### "Python not found"

Install Python from https://www.python.org/downloads/
- Download Python 3.10 or higher
- Check "Add Python to PATH" during installation

### "Access Denied"

Run the installer as Administrator:
1. Right-click `install.bat` or PowerShell
2. Select "Run as administrator"

### "Claude Desktop config not found"

1. Start Claude Desktop once to create config directory
2. Manually create: `%APPDATA%\Claude\claude_desktop_config.json`
3. Add the memopad configuration

### Server not responding in Claude

1. **Check logs:**
   Claude Desktop → Settings → Advanced → View Logs

2. **Test server manually:**
   ```powershell
   cd F:\ANTI\memopad
   python server.py
   ```
   Type: `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`
   Press Enter (you should see a JSON response)

3. **Verify config:**
   ```powershell
   notepad %APPDATA%\Claude\claude_desktop_config.json
   ```

4. **Restart Claude Desktop completely** (exit from system tray)

---

## Usage Examples

Once installed and configured, you can use these commands in Claude:

### Create Notes
```
Create a note titled "Shopping List" with content "Milk, Eggs, Bread"
```

### List Notes
```
Show me all my notes
List my notes
What notes do I have?
```

### Get Specific Note
```
Show me note 1
Get note with ID 3
```

### Update Notes
```
Update note 2 to have title "Updated Title"
Change the content of note 1 to "New content here"
```

### Delete Notes
```
Delete note 3
Remove note 5
```

---

## Data Location

Your notes are stored at:
```
%USERPROFILE%\.memopad\notes.json
```

Typically: `C:\Users\YourUsername\.memopad\notes.json`

### Backup Your Notes

```powershell
# Create backup
Copy-Item "$env:USERPROFILE\.memopad\notes.json" "$env:USERPROFILE\.memopad\notes.json.backup"

# Restore from backup
Copy-Item "$env:USERPROFILE\.memopad\notes.json.backup" "$env:USERPROFILE\.memopad\notes.json"
```

---

## Uninstallation

```powershell
# Remove installation
Remove-Item -Recurse -Force F:\ANTI\memopad

# Remove data (WARNING: Deletes all notes!)
Remove-Item -Recurse -Force "$env:USERPROFILE\.memopad"

# Remove from Claude config (manual)
notepad %APPDATA%\Claude\claude_desktop_config.json
# Delete the "memopad" section
```

---

## Support

1. **Check README.md** for full documentation
2. **Read QUICKSTART.md** for usage guide
3. **Review MEMOPAD_ANALYSIS.md** for technical details
4. **Run verify_installation.py** to diagnose issues
5. **Check Claude Desktop logs** for error messages

---

## Security Note

⚠️ **Notes are stored in plain text** on your local filesystem.

For sensitive data:
- Enable BitLocker (Windows drive encryption)
- Use encrypted external storage
- Implement custom encryption (requires modifying server)

---

## What's Fixed in This Version

✅ **15 Critical Bug Fixes:**
1. File corruption prevention (atomic writes)
2. Race condition handling (async locks)
3. Automatic backup on JSON errors
4. Input validation (prevents crashes)
5. Unicode/emoji support
6. Proper ID generation
7. Complete MCP protocol compliance
8. Comprehensive error handling
9. Detailed logging
10. Thread-safe operations
11. Graceful shutdown
12. Storage initialization
13. Proper encoding (UTF-8)
14. JSON-RPC 2.0 compliance
15. Robust file recovery

---

## Next Steps After Installation

1. ✅ Restart Claude Desktop
2. ✅ Test basic functionality
3. ✅ Create your first note
4. ✅ Read QUICKSTART.md for tips
5. ✅ Set up regular backups

---

**Happy note-taking! 📝**
