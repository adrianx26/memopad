"""Backlinks tool for Memopad MCP server.

Returns all relations pointing TO a given entity. Complements `build_context`,
which traverses outgoing relations.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description=(
        "List all notes that link TO the given identifier (incoming relations). "
        "Includes unresolved [[wikilinks]] that haven't been resolved yet."
    ),
)
async def backlinks(
    identifier: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Find all notes linking to the given identifier.

    Args:
        identifier: Permalink, title, or external_id of the target note.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown list grouped by relation type. Resolved and unresolved
        backlinks are both shown; unresolved entries are tagged.

    Examples:
        backlinks("coffee-brewing-methods")
        backlinks("Coffee Brewing Methods", project="cooking")
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=backlinks identifier={identifier} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        # Resolve identifier → external_id, so the caller can pass any of
        # permalink / title / path / external_id and we still query correctly.
        try:
            entity_id = await knowledge_client.resolve_entity(identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve identifier '{identifier}': {e}"

        data = await knowledge_client.get_backlinks(entity_id)
        items: list[dict] = data.get("backlinks", [])
        target_title = data.get("target_title", identifier)

        if not items:
            summary = f"# Backlinks for '{target_title}'\n\n_No notes link to this entity yet._"
            return add_project_metadata(summary, active_project.name)

        # Group by relation_type — gives a clearer picture than a flat list.
        # Guard against rows missing relation_type so a single malformed row
        # doesn't KeyError the whole tool.
        by_type: dict[str, list[dict]] = {}
        for item in items:
            rel_type = item.get("relation_type") or "related"
            by_type.setdefault(rel_type, []).append(item)

        lines = [
            f"# Backlinks for '{target_title}'",
            f"project: {active_project.name}",
            f"total: {len(items)}",
            "",
        ]
        for rel_type, group in sorted(by_type.items()):
            lines.append(f"## {rel_type} ({len(group)})")
            for item in group:
                title = item.get("from_title") or "(unknown)"
                permalink = item.get("from_permalink") or ""
                marker = "" if item.get("resolved") else " _[unresolved]_"
                ctx = f" — {item['context']}" if item.get("context") else ""
                lines.append(f"- [[{permalink or title}|{title}]]{marker}{ctx}")
            lines.append("")

        return add_project_metadata("\n".join(lines), active_project.name)
