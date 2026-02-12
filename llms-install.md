# MemoPad Installation Guide for LLMs

This guide is specifically designed to help AI assistants like Cline install and configure MemoPad. Follow these
steps in order.

## Installation Steps

### 1. Install MemoPad Package

Use one of the following package managers to install:

```bash
# Install with uv (recommended)
uv tool install memopad

# Or with pip
pip install memopad
```

### 2. Configure MCP Server

Add the following to your config:

```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": [
        "memopad",
        "mcp"
      ]
    }
  }
}
```

For Claude Desktop, this file is located at:

macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
Windows: %APPDATA%\Claude\claude_desktop_config.json

### 3. Start Synchronization (optional)

To synchronize files in real-time, run:

```bash
memopad sync --watch
```

Or for a one-time sync:

```bash
memopad sync
```

## Configuration Options

### Custom Directory

To use a directory other than the default `~/memopad`:

```bash
memopad project add custom-project /path/to/your/directory
memopad project default custom-project
```

### Multiple Projects

To manage multiple knowledge bases:

```bash
# List all projects
memopad project list

# Add a new project
memopad project add work ~/work-memopad

# Set default project
memopad project default work
```

## Importing Existing Data

### From Claude.ai

```bash
memopad import claude conversations path/to/conversations.json
memopad import claude projects path/to/projects.json
```

### From ChatGPT

```bash
memopad import chatgpt path/to/conversations.json
```

### From MCP Memory Server

```bash
memopad import memory-json path/to/memory.json
```

## Troubleshooting

If you encounter issues:

1. Check that MemoPad is properly installed:
   ```bash
   memopad --version
   ```

2. Verify the sync process is running:
   ```bash
   ps aux | grep memopad
   ```

3. Check sync output for errors:
   ```bash
   memopad sync --verbose
   ```

4. Check log output:
   ```bash
   cat ~/.memopad/memopad.log
   ```

For more detailed information, refer to the [full documentation](https://memory.basicmachines.co/).