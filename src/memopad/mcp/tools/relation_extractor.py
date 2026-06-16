"""Relation extraction tool for Memopad MCP server."""

import textwrap
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
    """Analyze a note and instruct the LLM to add wikilinks to existing entities.
    
    This tool fetches the content of a specific note and a list of all known
    entity titles in the project. It returns a prompt directing the host LLM 
    to scan the text for mentions of those entities and use the `edit_note` 
    tool to convert them into `[[Wikilinks]]`.
    
    Args:
        permalink: The permalink of the note to analyze.
        project: Project name. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
        
    Returns:
        A markdown formatted instruction for the host LLM containing the note content
        and a list of available entity titles.
    """
    logger.info(f"Generating relation extraction instructions for {permalink} in project {project}")

    async with get_client() as client:
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
            
        # Fetch all entity titles (we can use search to get everything, or a specific endpoint if one existed)
        # Using search with no criteria to get all entities (or at least a large batch)
        # Wait, if there are thousands, this might be too large. We can just ask the LLM to suggest relations
        # based on what it knows, OR we can fetch a limited list of recently modified entities, or ask it to 
        # use `search` to find them. The best approach is to instruct the LLM to identify key concepts and 
        # use `search` or `build_context` to verify they exist before linking.
        
        prompt = textwrap.dedent(f"""
        # Relation Extraction Task
        
        You have been requested to extract relations for the note `{permalink}` in project `{active_project.name}`.
        
        ## Note Content
        ```markdown
        {content}
        ```
        
        ## Instructions
        1. Read the note content and identify key concepts, proper nouns, or important terms that likely exist as other notes in this project.
        2. Use the `search_notes` tool to check if these concepts actually exist as entities.
        3. For any concepts that exist, use the `edit_note` tool (with `replace_section` or `find_replace`) to wrap the mentions in the text with wikilinks (e.g., `[[Concept Name]]`).
        4. Alternatively, you can add explicit relation bullet points at the bottom of the file (e.g., `- depends_on [[Concept Name]]`).
        """).strip()

        return prompt
