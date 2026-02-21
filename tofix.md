# Known Issues & Fixes

Below is a summary of the issues encountered and resolved during the Memopad assimilation stabilization.

| ID | Issue | Category | Status |
|----|-------|----------|--------|
| 1 | `NotImplementedError` on Windows | Windows / Loop | ✅ Fixed |
| 2 | `pop from an empty deque` | Windows / DB | ✅ Fixed |
| 3 | `FunctionTool` not callable | API Usage | ✅ Resolved |
| 4 | No project specified | API Usage | ✅ Resolved |
| 5 | `CancelledError` on long runs | Stability | ✅ Fixed |
| 6 | `EntityCreationError` on retry | Stability | ✅ Fixed |

## 1. `mcp_memopad_assimilate` Fails on Windows with `NotImplementedError`

**Date Encountered:** 2026-02-20

### Description

When calling the `mcp_memopad_assimilate` MCP tool directly on Windows, GitHub repository assimilation fails with the following error:

```
Could not fetch content from <URL>:
- Unexpected error processing repository <URL>: NotImplementedError:
```

### Root Cause

The MCP tool internally uses an async event loop mechanism that is not compatible with Windows (likely related to `winloop` or an unsupported event loop policy on Windows).

### Workaround

Use the `run_assimilate.py` script directly instead of the MCP tool:

```powershell
python run_assimilate.py https://github.com/<owner>/<repo>
```

Run from the `c:\ANTI\memopad` directory. This script correctly handles the Windows event loop and completes successfully.

### Fix Applied

Added `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` at module level in `src/memopad/mcp/server.py`. This ensures the MCP server uses the stable `SelectorEventLoop` on Windows, which handles `aiosqlite` and subprocesses more reliably.

### Status

✅ **Fixed** — MCP tool now works correctly on Windows.

### Related Conversations

- `2bfed7fd-1363-4a2c-8cca-de7927f93472` — Troubleshooting Memopad MCP Server Error (root cause analysis)
- `974cf84b-1c46-41e1-adb2-162cc2bbcb68` — Fixing Assimilate Tool (robustness improvements)

---

## 2. `IndexError: pop from an empty deque` on Windows during DB Cleanup

**Date Encountered:** 2026-02-21

### Description

When running CLI commands (e.g., `mp project list`) or scripts that interact with the database on Windows, the process may crash during event loop teardown with:

```
Exception in thread Thread-2:
Traceback (most recent call last):
  File "C:\Users\shobymik\Anaconda3\lib\site-packages\aiosqlite\core.py", line 107, in run
IndexError: pop from an empty deque
```

### Root Cause

On Windows, the default `ProactorEventLoop` has known issues with `aiosqlite` during shutdown. If there are pending handles when the loop is closing, it can trigger this error in the aiosqlite worker thread.

### Fix Applied

Same fix as Issue 1: `WindowsSelectorEventLoopPolicy` is now set in `server.py` at module load time. This prevents the `ProactorEventLoop` from being used, which was causing `aiosqlite` to crash during cleanup.

### Status

✅ **Fixed** — CLI and scripts no longer crash during DB cleanup on Windows.

---

## 3. `TypeError: 'FunctionTool' object is not callable` when using `assimilate`

**Date Encountered:** 2026-02-21

### Description

Attempting to call the `assimilate` function directly from a script (e.g., `run_assimilate.py`) fails because it is wrapped by `@mcp.tool()`.

### Fix Applied

Use `_assimilate_impl` directly from `memopad.mcp.tools.assimilate` when calling from scripts.

### Status

✅ **Resolved** — Scripts now call the implementation function directly.

---

## 4. `ValueError: No project specified` during assimilation

**Date Encountered:** 2026-02-21

### Description

When calling `assimilate` without a `project` argument and `default_project_mode` is false, it raises `ValueError`.

### Fix Applied

Ensure `project="main"` (or another valid project name) is passed to the `assimilate` call.

### Status

✅ **Resolved** — Scripts now explicitly specify the target project.

---

## 5. `asyncio.exceptions.CancelledError` during long-running assimilation

**Date Encountered:** 2026-02-21

### Description

The assimilation process is frequently interrupted by `asyncio.exceptions.CancelledError` on Windows, especially during `git clone` or database operations like `PRAGMA journal_mode=WAL`.

### Root Cause / Investigation Needed

Likely related to how tasks are managed/cancelled in the environment's event loop when they exceed a certain duration or during subprocess execution on Windows.

### Workaround

- Manually cloning the repository before assimilation.
- Limiting `max_pages`.
- Retrying the script (it resumes partially due to existing files).

### Fix Applied

1. Moved `WindowsSelectorEventLoopPolicy` to module level (before `asyncio.run()`) in `assimilate_aionui.py` and `assimilate_kittentts.py`.
2. Added `asyncio.CancelledError` catch in `_clone_github_repo` (returns descriptive error instead of crashing).
3. Added `asyncio.CancelledError` catch in `_assimilate_impl` top-level handler (returns user-friendly error with retry advice).

### Status

✅ **Fixed** — event loop policy is now set correctly before the loop starts, and `CancelledError` is handled gracefully.

---

## 6. `memopad.services.exceptions.EntityCreationError` on existing files

**Date Encountered:** 2026-02-21

### Description

During retries of assimilation after a crash/cancellation, `EntityCreationError` is raised with message `file for entity ... already exists`.

### Root Cause

The `fast_write_entity` method raises an error if the physical file already exists, even if the database state is being reconciled.

### Fix Needed

Improve `fast_write_entity` to handle existing files gracefully (e.g., overwrite if permitted or update the existing one) instead of raising a hard error during assimilation retries.

### Fix Applied

Changed `fast_write_entity` in `src/memopad/services/entity_service.py` to log a warning and proceed (overwrite) instead of raising `EntityCreationError` when the target file already exists. This allows assimilation retries to complete gracefully.

### Status

✅ **Fixed** — `fast_write_entity` now overwrites existing files with a warning log.
