# Testing MemoPad MCP Server

## Quick Start Test

### 1. Verify Installation
```bash
# Check if memopad is installed
memopad --help

# Check version
memopad --version
```

### 2. Start MCP Server (Manual Test)
```bash
# Start the server
memopad mcp

# You should see it waiting for input
# Press Ctrl+C to stop
```

### 3. Configure Claude Desktop

**File Location:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Configuration:**
```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": ["memopad", "mcp"]
    }
  }
}
```

**With Specific Project:**
```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": ["memopad", "mcp", "--project", "my-project-name"]
    }
  }
}
```

### 4. Verify in Claude Desktop

After restarting Claude Desktop:

1. **Check for tools icon** (🔌) in the interface
2. **Test with a simple prompt:**
   ```
   Create a test note about today's date
   ```
3. **Verify the note was created:**
   ```
   List my recent notes
   ```

### 5. Check the Default Project Directory

```bash
# Default location
# Windows: C:\Users\<username>\.memopad
# macOS/Linux: ~/.memopad

# Or check with command
memopad status
```

## Troubleshooting

### MCP Server Not Starting

1. **Check installation:**
   ```bash
   which memopad  # macOS/Linux
   where memopad  # Windows
   ```

2. **Reinstall if needed:**
   ```bash
   uv tool uninstall memopad
   uv tool install memopad
   ```

3. **Check logs:**
   - Location: `~/.memopad/memopad.log`
   ```bash
   tail -f ~/.memopad/memopad.log  # macOS/Linux
   Get-Content ~\.memopad\memopad.log -Wait  # Windows
   ```

### Claude Desktop Not Showing Tools

1. **Verify config file syntax** (valid JSON)
2. **Restart Claude Desktop** completely
3. **Check Claude Desktop logs**
4. **Try with absolute path:**
   ```json
   {
     "mcpServers": {
       "memopad": {
         "command": "C:\\Users\\<username>\\.local\\bin\\memopad.exe",
         "args": ["mcp"]
       }
     }
   }
   ```

### Permission Issues

```bash
# Make sure memopad is executable
chmod +x $(which memopad)  # macOS/Linux
```

## Advanced Configuration

### Using Environment Variables

```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": ["memopad", "mcp"],
      "env": {
        "MEMOPAD_LOG_LEVEL": "DEBUG",
        "MEMOPAD_HOME": "/custom/path"
      }
    }
  }
}
```

### Multiple Projects Setup

```json
{
  "mcpServers": {
    "memopad-work": {
      "command": "uvx",
      "args": ["memopad", "mcp", "--project", "work"]
    },
    "memopad-personal": {
      "command": "uvx",
      "args": ["memopad", "mcp", "--project", "personal"]
    }
  }
}
```

## Example Usage in Claude

Once configured, you can ask Claude:

```
1. "Create a note about Python async programming"
2. "Search my notes for 'database'"
3. "What have I been working on this week?"
4. "Show me notes related to the API project"
5. "Create a visualization of my project components"
```

## MCP Server Architecture

The MCP server runs as a subprocess from Claude Desktop:
- **Communication**: JSON-RPC over stdio
- **Protocol**: Model Context Protocol (MCP)
- **Tools**: Exposed via FastMCP
- **Storage**: Local SQLite database + markdown files

## Next Steps

1. ✅ Install/Update MemoPad
2. ✅ Configure Claude Desktop
3. ✅ Restart Claude
4. ✅ Test basic operations
5. ✅ Start using it for knowledge management!
