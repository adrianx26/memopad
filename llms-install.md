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

After installing, verify the package actually works — a "successful" install is
not a working install (a broken package can install cleanly but fail to start):

```bash
memopad --version
```

This must print `MemoPad version: …`. If it prints a traceback / `NameError` /
`ModuleNotFoundError` instead, the package is broken — reinstall or report the
issue before configuring MCP. This catches the class of bug where a router
references an unimported name that only errors at runtime on the installed
Python.

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

### Distillation (L0→L3 memory pyramid)

MemoPad automatically distils structured observations into L1 atomic facts → L2
scenarios → an L3 persona on every write (pure in-app code, no external model). You
can also drive it on demand or backfill an existing knowledge base:

```bash
memopad distill                 # incremental pass (default)
memopad distill --bulk          # cold-start / backfill: process ALL existing notes
memopad distill --dry-run       # read-only: show current L1/L2/L3 counts
memopad distill discover-categories   # show which observation categories are distillable
memopad distill add-categories --auto  # widen the distillable set to all unknown categories
```

Observation categories are free-form; comma-separated compound categories match if
any component is distillable. A stuck watermark (L1 empty though notes exist)
self-heals — no manual `--bulk` needed. Enable the pipeline with
`levels_enabled: true` (and `levels_pipeline_automatic: true` for the auto cadence)
in `~/memopad/config.json`.

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

For more detailed information, refer to the [full documentation](https://memory.xxx/).