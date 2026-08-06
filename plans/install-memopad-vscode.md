# Install MemoPad MCP Server on Visual Studio Code

## Overview

This guide will walk you through installing and configuring the MemoPad MCP (Model Context Protocol) server for Visual Studio Code. MemoPad enables bidirectional communication between LLMs and your local Markdown files, creating a personal knowledge graph accessible directly from VS Code.

## Prerequisites

- Visual Studio Code (latest version)
- Python 3.12+ (with pip or uv package manager)
- Git (optional, for cloning repository)

## Installation Steps

### Step 1: Install VS Code MCP Extension

1. Open Visual Studio Code
2. Go to the Extensions view (`Ctrl+Shift+X` or `Cmd+Shift+X`)
3. Search for "Model Context Protocol" or "MCP"
4. Install the official MCP extension (publisher: Model Context Protocol)
5. Reload VS Code after installation

### Step 2: Install MemoPad Package

#### Option A: Install via pip (Recommended)

```bash
pip install memopad
```

#### Option B: Install via uv (Fastest, with virtual environment)

```bash
uv tool install memopad
```

#### Option C: Install from Source (Developer Mode)

```bash
# Clone the repository
git clone https://github.com/adrianx26/memopad.git
cd memopad

# Install with dev dependencies
pip install -e ".[dev]"

# Or using uv
uv venv
uv pip install -e .[dev]
```

### Step 3: Configure VS Code MCP Settings

1. Open VS Code User Settings (JSON) by pressing `Ctrl+Shift+P` and typing "Preferences: Open User Settings (JSON)"
2. Add the following configuration:

```json
{
  "mcp": {
    "servers": {
      "memopad": {
        "command": "uvx",
        "args": ["memopad", "mcp"]
      }
    }
  }
}
```

**Alternative Configuration (using pip):**

```json
{
  "mcp": {
    "servers": {
      "memopad": {
        "command": "python",
        "args": ["-m", "memopad", "mcp"]
      }
    }
  }
}
```

**Workspace-Specific Configuration:**
Create a `.vscode/mcp.json` file in your workspace:

```json
{
  "servers": {
    "memopad": {
      "command": "uvx",
      "args": ["memopad", "mcp"]
    }
  }
}
```

### Step 4: Test MemoPad Server

1. Restart VS Code to apply the settings
2. Open a new or existing Markdown file
3. Press `Ctrl+Shift+P` and type "MCP: List Servers"
4. You should see "memopad" listed as an available server
5. Press `Ctrl+Shift+P` and type "MCP: Open Console" to verify connection

### Step 5: Verify Installation

Run these commands in your terminal to verify MemoPad is working:

```bash
# Check MemoPad version
memopad --version

# Run MemoPad server manually (for debugging)
memopad mcp

# Check sync status
memopad status

# Run doctor check (file <-> DB consistency)
memopad doctor
```

### Step 6: Create Your First Note

1. Open VS Code's MCP console
2. Try these commands:
   - "Create a note titled 'VS Code Setup' with content 'Installed MemoPad MCP server'"
   - "List all my notes"
   - "Search for notes about VS Code"

## Configuration Options

### Custom Project Configuration

To use a specific project directory:

```json
{
  "mcp": {
    "servers": {
      "memopad": {
        "command": "uvx",
        "args": ["memopad", "mcp", "--project", "your-project-name"]
      }
    }
  }
}
```

### Environment Variables

```bash
# Enable debug logging
MEMOPAD_LOG_LEVEL=DEBUG memopad mcp

# Force local mode (ignore cloud configuration)
MEMOPAD_FORCE_LOCAL=true memopad mcp
```

## Troubleshooting

### Common Issues

1. **"Command not found" error:**
   - Ensure Python and MemoPad are in your PATH
   - Try reinstalling with `uv tool install memopad`

2. **MCP server not connecting:**
   - Check VS Code console for errors
   - Verify Python version is 3.12+
   - Check that the MCP extension is properly installed

3. **Notes not saving:**
   - Check `~/.memopad/memopad.log` for errors
   - Verify file permissions on `~/.memopad/` directory

4. **Sync issues:**
   - Run `memopad doctor` to check consistency
   - Run `memopad sync` to manually sync files

### Log Location

Logs are stored at: `~/.memopad/memopad.log`

```bash
# View log file
tail -f ~/.memopad/memopad.log
```

## Uninstallation

### Remove MemoPad Package

```bash
# pip
pip uninstall memopad

# uv
uv tool uninstall memopad
```

### Remove VS Code Configuration

1. Open VS Code User Settings (JSON)
2. Remove the `mcp.servers.memopad` section

### Remove Data

```bash
# Remove notes and configuration
rm -rf ~/.memopad
```

## Next Steps

- Read the [User Guide](https://docs.memopad.xxx/user-guide/) for advanced features
- Explore [CLI commands](https://docs.memopad.xxx/guides/cli-reference/)
- Learn about [cloud sync](https://docs.memopad.xxx/guides/cloud-cli/)

## Resources

- [MemoPad Documentation](https://docs.memopad.xxx)
- [GitHub Repository](https://github.com/adrianx26/memopad)
- [MCP Protocol](https://modelcontextprotocol.io/)
- [VS Code Extension](https://marketplace.visualstudio.com/items?itemName=modelcontextprotocol.mcp)

## Support

If you encounter issues:
1. Check the [FAQ](https://docs.memopad.xxx/faq/)
2. Review the [troubleshooting guide](https://docs.memopad.xxx/troubleshooting/)
3. Create an issue on [GitHub](https://github.com/adrianx26/memopad/issues)

---

**Happy note-taking in VS Code!** 🚀
