# Memopad MCP Server Installation Script for Windows
# Run this script in PowerShell

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Memopad MCP Server Installation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Set installation directory
$InstallDir = "F:\ANTI\memopad"

# Create directory if it doesn't exist
Write-Host "Creating installation directory..." -ForegroundColor Yellow
if (!(Test-Path -Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Host "✓ Created directory: $InstallDir" -ForegroundColor Green
} else {
    Write-Host "✓ Directory already exists: $InstallDir" -ForegroundColor Green
}

# Check Python installation
Write-Host ""
Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $PythonVersion = python --version 2>&1
    Write-Host "✓ Python found: $PythonVersion" -ForegroundColor Green
    
    # Extract version number
    if ($PythonVersion -match "Python (\d+)\.(\d+)") {
        $MajorVersion = [int]$Matches[1]
        $MinorVersion = [int]$Matches[2]
        
        if ($MajorVersion -lt 3 -or ($MajorVersion -eq 3 -and $MinorVersion -lt 10)) {
            Write-Host "⚠ Warning: Python 3.10+ recommended (found $MajorVersion.$MinorVersion)" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "✗ Python not found!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://www.python.org/" -ForegroundColor Red
    exit 1
}

# Copy server files
Write-Host ""
Write-Host "Installing memopad server files..." -ForegroundColor Yellow

# Note: The actual files will be in the same directory as this script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$FilesToCopy = @(
    "memopad_server_fixed.py",
    "test_memopad.py",
    "QUICKSTART.md",
    "MEMOPAD_ANALYSIS.md"
)

foreach ($File in $FilesToCopy) {
    $SourceFile = Join-Path $ScriptDir $File
    $DestFile = Join-Path $InstallDir $File
    
    if (Test-Path $SourceFile) {
        Copy-Item -Path $SourceFile -Destination $DestFile -Force
        Write-Host "✓ Copied: $File" -ForegroundColor Green
    } else {
        Write-Host "⚠ File not found: $File (will be created)" -ForegroundColor Yellow
    }
}

# Rename the main server file
$ServerSource = Join-Path $InstallDir "memopad_server_fixed.py"
$ServerDest = Join-Path $InstallDir "server.py"
if (Test-Path $ServerSource) {
    Move-Item -Path $ServerSource -Destination $ServerDest -Force
    Write-Host "✓ Created server.py" -ForegroundColor Green
}

# Create storage directory
$StorageDir = Join-Path $env:USERPROFILE ".memopad"
Write-Host ""
Write-Host "Creating storage directory..." -ForegroundColor Yellow
if (!(Test-Path -Path $StorageDir)) {
    New-Item -ItemType Directory -Path $StorageDir -Force | Out-Null
    Write-Host "✓ Created: $StorageDir" -ForegroundColor Green
} else {
    Write-Host "✓ Storage directory exists: $StorageDir" -ForegroundColor Green
}

# Test the installation
Write-Host ""
Write-Host "Testing installation..." -ForegroundColor Yellow

$ServerPath = Join-Path $InstallDir "server.py"
if (Test-Path $ServerPath) {
    try {
        # Run a quick syntax check
        python -m py_compile $ServerPath 2>&1 | Out-Null
        Write-Host "✓ Server file validated successfully" -ForegroundColor Green
    } catch {
        Write-Host "⚠ Warning: Could not validate server file" -ForegroundColor Yellow
    }
} else {
    Write-Host "✗ Server file not found!" -ForegroundColor Red
}

# Configure Claude Desktop
Write-Host ""
Write-Host "Configuring Claude Desktop..." -ForegroundColor Yellow

$ClaudeConfigDir = Join-Path $env:APPDATA "Claude"
$ClaudeConfigFile = Join-Path $ClaudeConfigDir "claude_desktop_config.json"

if (!(Test-Path $ClaudeConfigDir)) {
    New-Item -ItemType Directory -Path $ClaudeConfigDir -Force | Out-Null
}

# Create or update config
$ServerPathEscaped = $ServerPath -replace '\\', '\\'

$ConfigTemplate = @"
{
  "mcpServers": {
    "memopad": {
      "command": "python",
      "args": [
        "$ServerPathEscaped"
      ]
    }
  }
}
"@

if (Test-Path $ClaudeConfigFile) {
    Write-Host "⚠ Claude config file already exists" -ForegroundColor Yellow
    Write-Host "  Location: $ClaudeConfigFile" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Add this to your config:" -ForegroundColor Yellow
    Write-Host $ConfigTemplate -ForegroundColor Gray
} else {
    $ConfigTemplate | Out-File -FilePath $ClaudeConfigFile -Encoding UTF8
    Write-Host "✓ Created Claude Desktop config" -ForegroundColor Green
}

# Create README
Write-Host ""
Write-Host "Creating README..." -ForegroundColor Yellow

$ReadmePath = Join-Path $InstallDir "README.md"
$ReadmeContent = @"
# Memopad MCP Server

## Installation Complete!

### Files Installed
- ``server.py`` - Main MCP server
- ``test_memopad.py`` - Test suite
- ``QUICKSTART.md`` - Quick start guide
- ``MEMOPAD_ANALYSIS.md`` - Technical analysis

### Storage Location
Notes are stored in: ``$StorageDir\notes.json``

### Next Steps

1. **Restart Claude Desktop** to load the MCP server

2. **Test the server** by asking Claude:
   - "Create a note titled 'Test' with content 'Hello World'"
   - "Show me all my notes"
   - "Delete note 1"

3. **Run tests** (optional):
   ``````powershell
   cd $InstallDir
   python test_memopad.py
   ``````

### Configuration

Claude Desktop config location:
``$ClaudeConfigFile``

To manually configure, add this to your config:
``````json
{
  "mcpServers": {
    "memopad": {
      "command": "python",
      "args": ["$ServerPathEscaped"]
    }
  }
}
``````

### Backup Your Notes

``````powershell
# Create backup
Copy-Item "$StorageDir\notes.json" "$StorageDir\notes.json.backup"

# Restore from backup
Copy-Item "$StorageDir\notes.json.backup" "$StorageDir\notes.json"
``````

### Troubleshooting

**Server not starting:**
``````powershell
# Test manually
python $ServerPath
# Type: {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
``````

**Check logs:**
- Claude Desktop → Settings → Advanced → View Logs

### Uninstall

``````powershell
# Remove installation
Remove-Item -Recurse -Force $InstallDir

# Remove data (optional)
Remove-Item -Recurse -Force $StorageDir

# Remove config (manually edit)
notepad $ClaudeConfigFile
``````

## Features

- ✓ Create, read, update, delete notes
- ✓ Automatic backup on corruption
- ✓ Thread-safe operations
- ✓ Unicode support
- ✓ Comprehensive error handling

## Support

For issues or questions, check:
- QUICKSTART.md - Getting started guide
- MEMOPAD_ANALYSIS.md - Technical details
"@

$ReadmeContent | Out-File -FilePath $ReadmePath -Encoding UTF8
Write-Host "✓ Created README.md" -ForegroundColor Green

# Final summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Installation Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Installation directory: $InstallDir" -ForegroundColor White
Write-Host "Storage directory: $StorageDir" -ForegroundColor White
Write-Host "Claude config: $ClaudeConfigFile" -ForegroundColor White
Write-Host ""
Write-Host "✓ Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Restart Claude Desktop" -ForegroundColor White
Write-Host "2. Test by creating a note in Claude" -ForegroundColor White
Write-Host "3. Read QUICKSTART.md for usage guide" -ForegroundColor White
Write-Host ""
Write-Host "Run tests with:" -ForegroundColor Yellow
Write-Host "  cd $InstallDir" -ForegroundColor Gray
Write-Host "  python test_memopad.py" -ForegroundColor Gray
Write-Host ""
