# Plan: Install MemoPad MCP Server to Antigravity

## Overview

This plan outlines the steps to install MemoPad as a global MCP server in Antigravity IDE.

## Current State Analysis

### MemoPad MCP Server
- **Package**: `memopad` (PyPI, version 0.18.0)
- **CLI Commands**: `memopad` or `mp`
- **MCP Command**: `memopad mcp`
- **Transport Support**: stdio (default), streamable-http, sse
- **Python Requirement**: Python 3.12+

### Antigravity Configuration
- **Config Location**: `c:/Users/shobymik/.gemini/antigravity/mcp_config.json`
- **Format**: Standard MCP server configuration with `mcpServers` object
- **Structure**: Uses `command`, `args`, and optional `env` properties

## Installation Options

### Option 1: Using uvx (Recommended)

This is the recommended approach as per MemoPad documentation. Uses `uv` package manager which handles dependencies cleanly.

**Prerequisites**: Install uv package manager
```bash
# Install uv if not already installed
pip install uv
```

**Configuration to add**:
```json
"memopad": {
  "command": "uvx",
  "args": [
    "memopad",
    "mcp"
  ]
}
```

### Option 2: Using pip (Direct Installation)

Install MemoPad globally and reference the command directly.

**Prerequisites**: Install memopad package
```bash
pip install memopad
```

**Configuration to add**:
```json
"memopad": {
  "command": "memopad",
  "args": [
    "mcp"
  ]
}
```

### Option 3: Using Docker (Isolated Environment)

Run MemoPad in a Docker container for complete isolation.

**Prerequisites**: Build Docker image
```bash
# Build the image from the MemoPad repository
docker build -t memopad-mcp:latest .
```

**Configuration to add**:
```json
"memopad": {
  "command": "docker",
  "args": [
    "run",
    "-i",
    "--rm",
    "-v",
    "c:\\:/mnt/c",
    "-v",
    "f:\\:/mnt/f",
    "-v",
    "%USERPROFILE%\\.memopad:/root/.memopad",
    "memopad-mcp:latest",
    "memopad",
    "mcp"
  ]
}
```

## Recommended Configuration

Based on the existing Antigravity setup and MemoPad documentation, **Option 1 (uvx)** is recommended because:

1. Matches the official MemoPad installation guide
2. Consistent with other MCP servers in the config (e.g., `sequential-thinking` uses npx)
3. Automatic dependency management
4. Easy updates via `uv tool upgrade memopad`

## Complete Updated Config File

```json
{
  "mcpServers": {
    "docker-demo": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-v",
        "c:\\:/mnt/c",
        "-v",
        "f:\\:/mnt/f",
        "mcp-server:latest"
      ]
    },
    "jules": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "JULES_API_KEY=AQ.Ab8RN6KwzkEjAnIanUuGnRLkAsvQyIGiFzI94gTd0wooXRe6Iw",
        "jules-mcp-server:latest"
      ]
    },
    "sequential-thinking": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sequential-thinking"
      ],
      "disabledTools": []
    },
    "genkit-mcp-server": {
      "command": "npx",
      "args": [
        "-y",
        "genkit-cli@^1.28.0",
        "mcp",
        "--explicitProjectRoot",
        "--no-update-notification",
        "--non-interactive"
      ],
      "env": {}
    },
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

## Post-Installation Steps

1. **Verify Installation**:
   ```bash
   uvx memopad --version
   ```

2. **Initialize Default Project** (if needed):
   ```bash
   memopad project list
   ```

3. **Test MCP Server**:
   - Restart Antigravity
   - The MemoPad MCP tools should now be available

## Available MCP Tools

Once installed, the following MemoPad tools will be available in Antigravity:

| Tool | Description |
|------|-------------|
| `write_note` | Create/update markdown notes |
| `read_note` | Read notes by title/permalink/URL |
| `read_content` | Read raw file content |
| `view_note` | View notes as formatted artifacts |
| `edit_note` | Edit notes incrementally |
| `move_note` | Move notes or directories |
| `delete_note` | Delete notes or directories |
| `build_context` | Navigate knowledge graph |
| `recent_activity` | Get recently updated info |
| `list_directory` | Browse directory contents |
| `search_notes` | Full-text search |
| `canvas` | Generate Obsidian canvas files |
| `list_memory_projects` | List available projects |
| `create_memory_project` | Create new projects |
| `delete_project` | Delete a project |

## Configuration Options

### Custom Project Path

To use a custom directory for MemoPad:

```json
"memopad": {
  "command": "uvx",
  "args": [
    "memopad",
    "mcp"
  ],
  "env": {
    "MEMOPAD_PROJECT_PATH": "f:/ANTI/memopad"
  }
}
```

### HTTP Transport (for remote access)

```json
"memopad": {
  "command": "uvx",
  "args": [
    "memopad",
    "mcp",
    "--transport",
    "streamable-http",
    "--port",
    "8000"
  ]
}
```

## Troubleshooting

1. **Command not found**: Ensure `uv` is installed and in PATH
2. **Permission errors**: Check file permissions for `~/.memopad` directory
3. **Sync issues**: Run `memopad doctor` to diagnose problems
