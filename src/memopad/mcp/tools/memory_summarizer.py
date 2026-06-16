"""Memory summarization tool for Memopad MCP server."""

import textwrap
from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.server import mcp
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project


@mcp.tool(
    description="Retrieve the 3 most relevant notes for a query and instruct the LLM to synthesize a summary.",
)
async def get_relevant_context(
    query: str,
    project: Optional[str] = None,
    limit: int = 3,
    context: Context | None = None,
) -> str:
    """Find highly relevant notes and instruct the LLM to summarize them.
    
    This tool performs a semantic/hybrid search for the user's query, fetches the full 
    content of the top matches, and returns a prompt directing the host LLM to 
    synthesize this information into a cohesive summary.
    
    Args:
        query: The topic or question to research in the knowledge base.
        project: Project name. Optional - server will resolve using hierarchy.
        limit: Maximum number of notes to retrieve for context (default: 3).
        context: Optional FastMCP context.
        
    Returns:
        A markdown formatted instruction containing the retrieved content and 
        summarization directions for the host LLM.
    """
    logger.info(f"Generating memory summarization for '{query}' in project {project}")

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)

        # Import here to avoid circular dependencies
        from memopad.mcp.clients import SearchClient, ResourceClient, KnowledgeClient
        
        # 1. Search for relevant notes using hybrid search
        search_client = SearchClient(client, active_project.external_id)
        resource_client = ResourceClient(client, active_project.external_id)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        
        try:
            # We use semantic/hybrid search for best results
            search_response = await search_client.semantic_search(query=query, mode="hybrid", limit=limit)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            # Fallback to FTS if semantic search isn't enabled
            search_response = await search_client.search(query={"query": query}, page_size=limit)
            
        results = search_response.results
        
        if not results:
            return f"# No Relevant Context Found\n\nNo notes were found matching the query: '{query}'."

        # 2. Fetch full content for each result
        compiled_context = ""
        for i, result in enumerate(results, 1):
            if not result.permalink:
                continue
                
            try:
                entity_id = await knowledge_client.resolve_entity(result.permalink)
                response = await resource_client.read(entity_id)
                content = response.text
                compiled_context += f"### Source {i}: {result.title} (`{result.permalink}`)\n```markdown\n{content}\n```\n\n"
            except Exception as e:
                logger.warning(f"Failed to read content for {result.permalink}: {e}")
                
        if not compiled_context:
            return "# Error\n\nFailed to retrieve content for the search results."

        # 3. Build the prompt for the LLM
        prompt = textwrap.dedent(f"""
        # Memory Summarization Task
        
        You have been requested to summarize the knowledge base context for the following query:
        **"{query}"**
        
        ## Retrieved Context
        The following notes are the most relevant matches found in the project `{active_project.name}`:
        
        {compiled_context}
        
        ## Instructions
        1. Read and analyze the provided context notes above.
        2. Synthesize a comprehensive summary that directly addresses the query "{query}".
        3. Use inline citations linking to the source notes using markdown links (e.g., [Title](memory://permalink)).
        4. If the retrieved context does not fully answer the query, clearly state what information is missing.
        5. Present your final summary directly to the user.
        """).strip()

        return prompt
