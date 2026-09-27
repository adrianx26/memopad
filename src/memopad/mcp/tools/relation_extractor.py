"""Relation extraction tool for Memopad MCP server."""

from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.server import mcp
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project


@mcp.tool(
    description="Analyze a note and suggest wikilinks to other existing notes.",
)
async def extract_relations(
    permalink: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Find existing entities mentioned by a note and return concrete suggestions.

    Args:
        permalink: The permalink of the note to analyze.
        project: Project name. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
    """
    logger.info(f"Extracting relation suggestions for {permalink} in project {project}")

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        entity_id = await knowledge_client.resolve_entity(permalink)
        suggestions = await knowledge_client.get_extraction_suggestions(entity_id)
        relations = suggestions["relations"]
        if not relations:
            return f"No existing entity mentions found in `{permalink}`."

        lines = [f"Relation suggestions for `{permalink}`:"]
        for item in relations:
            lines.append(
                f"- `[[{item['title']}]]` ({item['permalink']}), "
                f"matched `{item['matched_text']}`, confidence {item['confidence']:.0%}"
            )
        lines.append("\nSuggestions are not applied automatically; use `edit_note` to accept them.")
        return "\n".join(lines)
