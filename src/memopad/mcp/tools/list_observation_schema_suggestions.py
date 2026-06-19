"""List observation schema consolidation suggestions tool."""

from typing import Optional

from fastmcp import Context

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description="List rare observation categories that may be noise or duplicates.",
)
async def list_observation_schema_suggestions(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Return rare category consolidation suggestions for the active project.

    Suggestions are review hints only. MemoPad does not rename categories or rewrite
    markdown automatically; the LLM/user decides whether to consolidate.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import MemoryClient

        memory_client = MemoryClient(client, active_project.external_id)
        suggestions = await memory_client.list_observation_schema_suggestions()

    if not suggestions:
        return (
            f"## Observation Schema Consolidation Suggestions "
            f"(project: {active_project.name})\n\nNo rare categories need consolidation review."
        )

    rows = [
        "| Rare category | Frequency | Possible duplicate | Confidence |",
        "| --- | ---: | --- | --- |",
    ]
    for suggestion in suggestions:
        duplicate = suggestion.possible_duplicate_of or ""
        rows.append(
            f"| {suggestion.name} | {suggestion.frequency} | {duplicate} | "
            f"{suggestion.confidence} |"
        )

    return "\n".join(
        [
            f"## Observation Schema Consolidation Suggestions (project: {active_project.name})",
            "",
            *rows,
        ]
    )
