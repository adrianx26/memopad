# Windows MCP Server Initialization Issue

## Problem Description
On Windows, the Memopad MCP server failed to start with the error:
`calling 'initialize': EOF`

This error occurred because the MCP protocol uses standard input/output (stdio) pipes for communication. On Windows, `asyncio` requires the `ProactorEventLoopPolicy` to support asynchronous I/O on pipes.

## Root Cause
The file `src/memopad/db.py` contained a module-level override that forced the event loop policy to `WindowsSelectorEventLoopPolicy`:

```python
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

This override was intended to prevent "Event loop is closed" errors during shutdown, but `SelectorEventLoop` does not support pipes on Windows. Since `db.py` is imported during the initialization of the MCP server, this policy was applied globally, breaking the MCP server's transport layer.

## Resolution
The overrides in `src/memopad/db.py` and `src/memopad/services/initialization.py` were removed. The MCP server entry point (`src/memopad/mcp/server.py`) correctly sets `WindowsProactorEventLoopPolicy` for Windows, which is now respected.

## Verification
- Validated `aiosqlite` compatibility with `ProactorEventLoop` using `test_aiosqlite.py`.
- validted that `test_aiosqlite.py` passes on Python 3.12 (Python 3.14 alpha had unrelated `greenlet` issues).
