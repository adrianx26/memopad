from contextlib import asynccontextmanager, AbstractAsyncContextManager
from typing import AsyncIterator, Callable, Optional

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from memopad.api.app import app as fastapi_app





# Optional factory override for dependency injection
_client_factory: Optional[Callable[[], AbstractAsyncContextManager[AsyncClient]]] = None


def set_client_factory(factory: Callable[[], AbstractAsyncContextManager[AsyncClient]]) -> None:
    """Override the default client factory (for cloud app, testing, etc).

    Args:
        factory: An async context manager that yields an AsyncClient

    Example:
        @asynccontextmanager
        async def custom_client_factory():
            async with AsyncClient(...) as client:
                yield client

        set_client_factory(custom_client_factory)
    """
    global _client_factory
    _client_factory = factory


@asynccontextmanager
async def get_client() -> AsyncIterator[AsyncClient]:
    """Get an AsyncClient as a context manager.

    This function provides proper resource management for HTTP clients,
    ensuring connections are closed after use.
    
    Local mode only: Use ASGI transport for in-process requests to local FastAPI app.

    Usage:
        async with get_client() as client:
            response = await client.get("/path")

    Yields:
        AsyncClient: Configured HTTP client
    """
    if _client_factory:
        # Use injected factory (cloud app, tests)
        async with _client_factory() as client:
            yield client
    else:
        # Default: create based on config
        timeout = Timeout(
            connect=10.0,  # 10 seconds for connection
            read=30.0,  # 30 seconds for reading response
            write=30.0,  # 30 seconds for writing request
            pool=30.0,  # 30 seconds for connection pool
        )

        # Local mode: ASGI transport for in-process calls
        logger.info("Creating ASGI client for local Basic Memory API")
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
        ) as client:
            yield client


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    DEPRECATED: Use get_client() context manager instead for proper resource management.
    """
    # Configure timeout for longer operations like write_note
    # Default httpx timeout is 5 seconds which is too short for file operations
    timeout = Timeout(
        connect=10.0,  # 10 seconds for connection
        read=30.0,  # 30 seconds for reading response
        write=30.0,  # 30 seconds for writing request
        pool=30.0,  # 30 seconds for connection pool
    )

    # Default: use ASGI transport for local API (development mode)
    logger.info("Creating ASGI client for local Basic Memory API")
    return AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
    )
