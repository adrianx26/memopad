"""Typed internal API clients for MCP tools.

These clients encapsulate API paths, error handling, and response validation.
MCP tools become thin adapters that call these clients and format results.

Usage:
    from memopad.mcp.clients import KnowledgeClient, SearchClient

    async with get_client() as http_client:
        knowledge = KnowledgeClient(http_client, project_id)
        entity = await knowledge.create_entity(entity_data)
"""

from memopad.mcp.clients.knowledge import KnowledgeClient
from memopad.mcp.clients.search import SearchClient
from memopad.mcp.clients.memory import MemoryClient
from memopad.mcp.clients.directory import DirectoryClient
from memopad.mcp.clients.resource import ResourceClient
from memopad.mcp.clients.project import ProjectClient

__all__ = [
    "KnowledgeClient",
    "SearchClient",
    "MemoryClient",
    "DirectoryClient",
    "ResourceClient",
    "ProjectClient",
]
