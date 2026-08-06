"""Drill-down tool for Memopad MCP server (Tb G5).

Traces a distilled memory (L1/L2/L3) back to its ground-truth L0 sources by
following the `source_entities` frontmatter field (and `derived_from` relations as
a secondary path). This is the reversible side of the L0–L3 distillation pyramid:
from any high-level abstraction the caller can reach the raw evidence it was
distilled from.

Requires the L0–L3 levels convention to be in use (frontmatter `level` +
`source_entities`). It is read-only and harmless when those fields are absent —
it simply reports that no provenance is recorded.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description=(
        "Trace a distilled memory back to its ground-truth sources. "
        "Follows frontmatter `source_entities` (and `derived_from` relations) "
        "down to L0, returning a Markdown provenance chain with the file_path of "
        "each hop so bodies can be read via read_note. Use this to reach the raw "
        "evidence behind an L1 fact / L2 scenario / L3 persona."
    ),
)
async def drill_down(
    identifier: str,
    target_level: Optional[str] = "L0",
    max_depth: int = 5,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Trace provenance from a memory down to its sources (default L0).

    Args:
        identifier: Permalink, title, or external_id of the memory to trace from.
        target_level: Stop descending at this level. One of L0|L1|L2|L3.
            Default L0 (trace all the way to raw evidence).
        max_depth: Max recursion depth (1–10). Default 5; bounds cyclic provenance.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown provenance chain: each hop shows `[level] [[permalink|title]]`
        and its file_path. Unresolved sources are tagged _[unresolved]_.

    Examples:
        drill_down("fact-ohms-law")
        drill_down("persona-main", project="electrotehnica")
        drill_down("scenario-transformer-training", target_level="L1")
    """
    valid_levels = {"L0", "L1", "L2", "L3"}
    if target_level is not None and target_level not in valid_levels:
        # Fail fast on bad input rather than silently defaulting (AGENTS.md).
        return f"# Error\n\n`target_level` must be one of {sorted(valid_levels)}, got '{target_level}'."

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=drill_down identifier={identifier} "
            f"target_level={target_level} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        # Resolve identifier → external_id (permalink / title / path / external_id).
        try:
            entity_id = await knowledge_client.resolve_entity(identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve identifier '{identifier}': {e}"

        data = await knowledge_client.drill_down(
            entity_id,
            target_level=target_level or "L0",
            max_depth=max_depth,
        )

    chain: str = data.get("chain", "")
    target_title = data.get("target_title", identifier)
    target_level_actual = data.get("target_level", "?")
    source_entities = data.get("source_entities", []) or []
    nodes = data.get("nodes", {}) or {}
    # Count hops (resolved + unresolved) from the structured tree.
    unresolved = 0

    def _count(node: dict) -> int:
        nonlocal unresolved
        kids = node.get("children", []) or []
        total = len(kids)
        for k in kids:
            if not k.get("resolved", True):
                unresolved += 1
            total += _count(k)
        return total

    hop_count = _count(nodes) if nodes else 0

    header = [
        f"# Drill-down for '{target_title}'",
        f"project: {active_project.name}",
        f"target level: `{target_level_actual}`",
    ]
    if source_entities:
        header.append(f"frontmatter sources: {len(source_entities)}")
    if hop_count:
        header.append(f"chain hops: {hop_count}" + (f" ({unresolved} unresolved)" if unresolved else ""))
    if not source_entities and not hop_count:
        header.append("")
        header.append(
            "_No provenance recorded. This memory has no `source_entities` in its "
            "frontmatter and no `derived_from` relations — it is either an L0 source "
            "itself or was created before the levels convention._"
        )

    body = "\n".join(header) + "\n\n" + (chain if chain.strip() else "_(empty chain)_")
    return add_project_metadata(body, active_project.name)