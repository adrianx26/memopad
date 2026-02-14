"""
Basic Memory FastMCP server.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    # Use SelectorEventLoop to avoid ProactorEventLoop cleanup issues
    # The ProactorEventLoop can raise "IndexError: pop from an empty deque" during
    # event loop cleanup when there are pending handles. SelectorEventLoop is more
    # stable for our use case (no subprocess pipes or named pipes needed).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastmcp import FastMCP
from loguru import logger

from memopad import db
from memopad.mcp.container import McpContainer, set_container
from memopad.services.initialization import initialize_app


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Lifecycle manager for the MCP server.

    Handles:
    - Database initialization and migrations
    - File sync via SyncCoordinator (if enabled and not in cloud mode)
    - Proper cleanup on shutdown
    """
    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = McpContainer.create()
    set_container(container)

    logger.debug(f"Starting Basic Memory MCP server (mode={container.mode.name})")

    # Track if we created the engine (vs test fixtures providing it)
    # This prevents disposing an engine provided by test fixtures when
    # multiple Client connections are made in the same test
    engine_was_none = db._engine is None

    # Initialize app (runs migrations, reconciles projects)
    await initialize_app(container.config)

    # Create and start sync coordinator (lifecycle centralized in coordinator)
    sync_coordinator = container.create_sync_coordinator()
    await sync_coordinator.start()

    try:
        yield
    finally:
        # Shutdown - coordinator handles clean task cancellation
        logger.debug("Shutting down Basic Memory MCP server")
        await sync_coordinator.stop()

        # Only shutdown DB if we created it (not if test fixture provided it)
        if engine_was_none:
            await db.shutdown_db()
            logger.debug("Database connections closed")
        else:  # pragma: no cover
            logger.debug("Skipping DB shutdown - engine provided externally")


mcp = FastMCP(
    name="Basic Memory",
    lifespan=lifespan,
)
