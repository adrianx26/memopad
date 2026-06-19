"""Auto-tagging tool for Memopad MCP server."""

import textwrap
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
    """Analyze a note and instruct the LLM to auto-tag it.
    
    This tool fetches the content of a specific note and returns a prompt directing
    the host LLM to analyze the content and use the `edit_note` tool to apply 
    appropriate semantic tags to the note's frontmatter.
    
    Args:
        permalink: The permalink of the note to tag.
        project: Project name. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
        
    Returns:
        A markdown formatted instruction for the host LLM containing the note content.
    """
    logger.info(f"Generating auto-tag instructions for {permalink} in project {project}")

    async with get_client() as client:
        # Resolve active project
        active_project = await get_active_project(client, project, context)

        # Import here to avoid circular dependencies
        from memopad.mcp.clients import KnowledgeClient, ResourceClient

        # Fetch the note content
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        resource_client = ResourceClient(client, active_project.external_id)
        
        try:
            entity_id = await knowledge_client.resolve_entity(permalink)
            response = await resource_client.read(entity_id)
            content = response.text
        except Exception as e:
            return f"# Error\n\nFailed to read note {permalink}: {e}"

        prompt = textwrap.dedent(f"""
        # Auto-Tagging Task
        
        You have been requested to auto-tag the note `{permalink}` in project `{active_project.name}`.
        
        ## Note Content
        ```markdown
        {content}
        ```
        
        ## Instructions
        1. Analyze the content above to determine its core topics, concepts, and themes.
        2. Generate 3-5 highly relevant, concise, lower-case tags (e.g., `architecture`, `planning`, `backend`).
        3. Use the `edit_note` tool to add these tags to the note. 
           - Set `operation` to `replace_section`.
           - If the note has frontmatter, update the `tags:` array.
           - If there's no frontmatter, add it.
        """).strip()

        return prompt
