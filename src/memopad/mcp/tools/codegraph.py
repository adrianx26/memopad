"""CodeGraph MCP tools (Tb G2).

Index source code into the existing knowledge graph and query it: find symbols,
compute impact paths (reverse `calls` BFS), and assemble a symbol's context. The
whole feature is gated by `codegraph_enabled` (default off); the server endpoint
enforces the flag, and these tools surface the disabled state as a clear error.
"""

from __future__ import annotations

from typing import List, Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description=(
        "Index a source code directory into the knowledge graph as code entities "
        "(file/function/class/module) with calls/defined_in/imports relations. "
        "Use this for cold-start context on a codebase. Gated by codegraph_enabled."
    ),
)
async def index_code(
    root: str,
    project: Optional[str] = None,
    languages: Optional[List[str]] = None,
    context: Context | None = None,
) -> str:
    """Parse a source tree and upsert code entities + relations.

    Args:
        root: Absolute or project-relative path of the source tree to index.
        project: Project name. Optional — server resolves the default.
        languages: Languages to index (default: python).
        context: Optional FastMCP context.

    Returns:
        A markdown summary of the index run (files / entities / relations).
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(f"MCP tool call tool=index_code root={root} project={active_project.name}")

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.index_codegraph(root, languages=languages)

    body = (
        f"# Code index complete\n\n"
        f"- **Files:** {data.get('files', 0)}\n"
        f"- **Entities:** {data.get('entities', 0)}\n"
        f"- **Relations:** {data.get('relations', 0)}\n"
        f"- **Skipped:** {data.get('skipped', 0)}\n\n"
        f"Use `find_symbol` to locate symbols, `impact_path` to see what a "
        f"change affects, and `code_context` for a symbol's definition + deps."
    )
    return add_project_metadata(body, active_project.name)


@mcp.tool(
    description=(
        "Find code symbols (functions/classes/modules) by name in the indexed "
        "code graph. Returns code:// permalinks for use with impact_path / code_context."
    ),
)
async def find_symbol(
    name: str,
    project: Optional[str] = None,
    exact: bool = False,
    context: Context | None = None,
) -> str:
    """Find symbols whose title matches `name` (case-insensitive substring).

    Args:
        name: Symbol name to search for.
        project: Project name. Optional — server resolves the default.
        exact: Require an exact title match.
        context: Optional FastMCP context.

    Returns:
        A markdown list of matching symbols with their `code://` permalinks.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.find_symbol(name, exact=exact)

    symbols = data.get("symbols", []) or []
    if not symbols:
        body = f"No symbols found matching '{name}'."
    else:
        lines = [f"# Symbols matching '{name}' ({len(symbols)})", ""]
        for s in symbols:
            q = f" — `{s.get('qualified_name')}`" if s.get("qualified_name") else ""
            lines.append(f"- `{s.get('permalink')}` ({s.get('entity_type')}){q}")
        lines.append(
            "\nUse `impact_path(permalink=...)` or `code_context(permalink=...)` next."
        )
        body = "\n".join(lines)
    return add_project_metadata(body, active_project.name)


@mcp.tool(
    description=(
        "Compute the impact path of changing a code symbol: BFS over reverse "
        "`calls` to find everything that (transitively) calls it. Answers "
        "'if I change X, what breaks?'. Gated by codegraph_enabled."
    ),
)
async def impact_path(
    permalink: str,
    project: Optional[str] = None,
    max_hops: int = 5,
    context: Context | None = None,
) -> str:
    """BFS over reverse `calls` from a symbol permalink.

    Args:
        permalink: The `code://` permalink of the symbol being changed.
        project: Project name. Optional — server resolves the default.
        max_hops: Max BFS hops (1–20). Default 5.
        context: Optional FastMCP context.

    Returns:
        Markdown listing dependents grouped by hop distance.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.impact_path(permalink, max_hops=max_hops)

    render = data.get("render") or ""
    count = data.get("count", 0)
    header = f"# Impact path for `{permalink}` ({count} dependents)\n\n"
    return add_project_metadata(header + (render or "_(no dependents)_"), active_project.name)


@mcp.tool(
    description=(
        "Get a code symbol's context: definition snippet + direct callers, "
        "callees, and imports. Token-budgeted for injection. Gated by codegraph_enabled."
    ),
)
async def code_context(
    permalink: str,
    project: Optional[str] = None,
    max_tokens: int = 0,
    context: Context | None = None,
) -> str:
    """Assemble definition + neighbors for a symbol permalink.

    Args:
        permalink: The `code://` permalink of the symbol.
        project: Project name. Optional — server resolves the default.
        max_tokens: Token budget for the rendered context (0 = unlimited).
        context: Optional FastMCP context.

    Returns:
        Markdown context (definition + callers/callees/imports).
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.code_context(permalink, max_tokens=max_tokens)

    render = data.get("render") or ""
    return add_project_metadata(render or "_(no context)_", active_project.name)