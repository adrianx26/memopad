"""Auto-tagging tool for Memopad MCP server."""

from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.server import mcp
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project


@mcp.tool(
    description="Analyze a note and generate semantic tags. Returns instructions to the LLM to apply the tags.",
)
async def auto_tag_note(
    permalink: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Suggest existing project tags from locally indexed related notes.

    Args:
        permalink: The permalink of the note to tag.
        project: Project name. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
    """
    logger.info(f"Generating local tag suggestions for {permalink} in project {project}")

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        entity_id = await knowledge_client.resolve_entity(permalink)
        suggestions = await knowledge_client.get_extraction_suggestions(entity_id)
        tags = suggestions["tags"]
        if not tags:
            return f"No existing project tags found for notes related to `{permalink}`."

        lines = [f"Existing tag suggestions for `{permalink}`:"]
        for item in tags:
            lines.append(
                f"- `{item['tag']}` (supported by {item['supporting_entities']} notes, "
                f"confidence {item['confidence']:.0%})"
            )
        lines.append("\nSuggestions are not applied automatically; use `edit_note` to accept them.")
        return "\n".join(lines)
