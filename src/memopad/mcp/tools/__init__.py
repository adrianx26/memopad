"""MCP tools for Memopad.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Import tools to register them with MCP
from memopad.mcp.tools.assimilate import assimilate
from memopad.mcp.tools.delete_note import delete_note
from memopad.mcp.tools.read_content import read_content
from memopad.mcp.tools.build_context import build_context
from memopad.mcp.tools.recent_activity import recent_activity
from memopad.mcp.tools.read_note import read_note
from memopad.mcp.tools.view_note import view_note
from memopad.mcp.tools.write_note import write_note
from memopad.mcp.tools.search import search_notes, search_by_metadata
from memopad.mcp.tools.canvas import canvas
from memopad.mcp.tools.list_directory import list_directory
from memopad.mcp.tools.edit_note import edit_note
from memopad.mcp.tools.move_note import move_note
from memopad.mcp.tools.project_management import (
    list_memory_projects,
    create_memory_project,
    delete_project,
)
from memopad.mcp.tools.optimize_storage import optimize_storage
from memopad.mcp.tools.daily_note import daily_note
from memopad.mcp.tools.backlinks import backlinks
from memopad.mcp.tools.semantic_search import semantic_search
from memopad.mcp.tools.graph_analytics import cluster_notes, hub_notes, find_path

# ChatGPT-compatible tools
from memopad.mcp.tools.chatgpt_tools import search, fetch

__all__ = [
    "assimilate",
    "backlinks",
    "build_context",
    "canvas",
    "cluster_notes",
    "create_memory_project",
    "daily_note",
    "delete_note",
    "delete_project",
    "edit_note",
    "fetch",
    "find_path",
    "hub_notes",
    "list_directory",
    "list_memory_projects",
    "move_note",
    "optimize_storage",
    "read_content",
    "read_note",
    "recent_activity",
    "search",
    "search_by_metadata",
    "search_notes",
    "semantic_search",
    "view_note",
    "write_note",
]
