"""List observation schemas tool for Memopad MCP server."""

from typing import Optional

from fastmcp import Context

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description="List canonical observation categories for the active project.",
)
async def list_observation_schemas(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Return a markdown table of observation categories and usage frequency.

    This MCP tool exposes the MemoPad-native Schema Layer inspired by MemGraphRAG:
    canonical categories, aliases, frequency, and rare/stable status. It is a
    visibility tool, not an enforcement tool.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import MemoryClient

        memory_client = MemoryClient(client, active_project.external_id)
        schemas = await memory_client.list_observation_schemas()

    if not schemas:
        return f"## Observation Schemas (project: {active_project.name})\n\nNo observation schemas registered yet."

    rows = ["| Category | Frequency | Aliases | Status |", "| --- | ---: | --- | --- |"]
    for schema in schemas:
        aliases = ", ".join(schema.aliases) if schema.aliases else ""
        status = "rare" if schema.status == "rare" else "stable"
        rows.append(
            f"| {schema.name} | {schema.frequency} | {aliases} | {status} |"
        )

    return "\n".join(
        [
            f"## Observation Schemas (project: {active_project.name})",
            "",
            *rows,
        ]
    )
