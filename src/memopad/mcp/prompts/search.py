"""Search prompts for Basic Memory MCP server.

These prompts help users search and explore their knowledge base.
"""

from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from memopad.config import ConfigManager
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project
from memopad.mcp.server import mcp
from memopad.mcp.tools.utils import call_post
from memopad.schemas.prompt import SearchPromptRequest


@mcp.prompt(
    name="search_knowledge_base",
    description="Search across all content in memopad",
)
async def search_prompt(
    query: str,
    timeframe: Annotated[
        Optional[str],
        Field(description="How far back to search (e.g. '1d', '1 week')"),
    ] = None,
) -> str:
    """Search across all content in memopad.

    This prompt helps search for content in the knowledge base and
    provides helpful context about the results.

    Args:
        query: The search text to look for
        timeframe: Optional timeframe to limit results (e.g. '1d', '1 week')

    Returns:
        Formatted search results with context
    """
    logger.info(f"Searching knowledge base, query: {query}, timeframe: {timeframe}")

    async with get_client() as client:
        config = ConfigManager().config
        active_project = await get_active_project(client, project=config.default_project)

        # Create request model
        request = SearchPromptRequest(query=query, timeframe=timeframe)

        # Call the prompt API endpoint
        response = await call_post(
            client,
            f"/v2/projects/{active_project.external_id}/prompt/search",
            json=request.model_dump(exclude_none=True),
        )

        # Extract the rendered prompt from the response
        result = response.json()
        return result["prompt"]
