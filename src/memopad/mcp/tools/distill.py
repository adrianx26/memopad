"""Distillation MCP tools (Tb L0-L3).

Surface the code-only distillation pipeline to the MCP client: manually trigger a
pass (`distill_memory`), and read back the distilled tiers (`list_facts`,
`list_scenarios`, `get_persona`). Distillation also runs automatically on every
write (the create-path + watch hooks nudge the scheduler); these tools are for
on-demand / inspection use. The engine is pure in-app code — no external
tool/API/key — so these tools just drive the same in-process pipeline.
"""

from __future__ import annotations

from typing import Optional

from fastmcp import Context
from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description=(
        "Run the L0->L1->L2->L3 distillation pipeline on demand. L1 distils new L0 "
        "observations into atomic facts; L2 clusters facts into scenarios; L3 "
        "aggregates stable facts into the persona. Distillation also runs "
        "automatically on every write — this tool is for on-demand / debug use. "
        "Pure in-app code (no external model/API/key).\n\n"
        "Use bulk=True to process ALL existing L0 entities (cold-start/backfill mode). "
        "After bulk mode completes, the watermark is set so future incremental passes "
        "only process new/changed entities. Use discover_categories() first to see what "
        "observation categories exist in your database that may need to be added."
    ),
)
async def distill_memory(
    level: str = "L1",
    project: Optional[str] = None,
    max_memories: int = 50,
    bulk: bool = False,
    context: Context | None = None,
) -> str:
    """Trigger a distillation pass for the given levels.

    Args:
        level: Comma-separated levels to run: "L1", "L2", "L3", or any combination
            (e.g. "L1,L2,L3"). Default "L1".
        project: Project name. Optional — server resolves the default.
        max_memories: Max L0 entities to scan per L1 pass (1–1000). Default 50.
        bulk: If True, process ALL existing L0 entities (cold-start/backfill mode).
              Default False = incremental (only updated-since-watermark).
        context: Optional FastMCP context.

    Returns:
        A markdown summary with per-level counts.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=distill_memory level={level} project={active_project.name} bulk={bulk}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.distill_memory(
            level, max_memories=max_memories, bulk=bulk
        )

    lines = ["# Distillation pass complete", ""]
    if "l1_facts" in data:
        lines.append(f"- **L1 facts:** {data['l1_facts']}")
    if "l2_scenarios" in data:
        lines.append(f"- **L2 scenarios:** {data['l2_scenarios']}")
    if "l3_persona" in data:
        lines.append(f"- **L3 persona:** {data['l3_persona']}")
    lines.append(
        "\nUse `list_facts`, `list_scenarios`, or `get_persona` to read back the tiers. "
        "Distillation also runs automatically on every `write_note` / file sync."
    )
    return add_project_metadata("\n".join(lines), active_project.name)


@mcp.tool(
    description=(
        "List distilled L1 atomic facts (entity_type=fact). Each carries `level: L1`, "
        "a confidence score, and `source_entities` provenance back to its L0 origins "
        "(drill_down reaches the source)."
    ),
)
async def list_facts(
    project: Optional[str] = None,
    limit: int = 200,
    context: Context | None = None,
) -> str:
    """List the distilled L1 facts for a project.

    Args:
        project: Project name. Optional — server resolves the default.
        limit: Max facts to return (1–1000). Default 200.
        context: Optional FastMCP context.

    Returns:
        A markdown list of facts with permalink, confidence, and category.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        facts = await knowledge_client.list_facts(limit=limit)

    if not facts:
        body = "No L1 facts distilled yet. Run `distill_memory(level='L1')` first."
        return add_project_metadata(body, active_project.name)

    lines = [f"# L1 facts ({len(facts)})", ""]
    for f in facts:
        md = f.get("entity_metadata") or {}
        conf = md.get("confidence")
        cat = md.get("category")
        meta = f" — `{cat}` conf={conf}" if (cat or conf) else ""
        lines.append(f"- `{f.get('permalink')}`{meta}: {f.get('title')}")
    return add_project_metadata("\n".join(lines), active_project.name)


@mcp.tool(
    description=(
        "List distilled L2 scenarios (entity_type=scenario). Each clusters related "
        "L1 facts (shared tag / source / similarity) and carries `level: L2` "
        "provenance back to its member facts."
    ),
)
async def list_scenarios(
    project: Optional[str] = None,
    limit: int = 200,
    context: Context | None = None,
) -> str:
    """List the distilled L2 scenarios for a project.

    Args:
        project: Project name. Optional — server resolves the default.
        limit: Max scenarios to return (1–1000). Default 200.
        context: Optional FastMCP context.

    Returns:
        A markdown list of scenarios with permalink and source count.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        scenarios = await knowledge_client.list_scenarios(limit=limit)

    if not scenarios:
        body = "No L2 scenarios distilled yet. Run `distill_memory(level='L2')` first."
        return add_project_metadata(body, active_project.name)

    lines = [f"# L2 scenarios ({len(scenarios)})", ""]
    for s in scenarios:
        md = s.get("entity_metadata") or {}
        n_sources = len(md.get("source_entities") or [])
        lines.append(
            f"- `{s.get('permalink')}` ({n_sources} facts): {s.get('title')}"
        )
    return add_project_metadata("\n".join(lines), active_project.name)


@mcp.tool(
    description=(
        "Get the distilled L3 persona for a project — the single aggregated "
        "summary of its stable L1 facts, grouped by category. Carries `level: L3` "
        "provenance back to every stable fact it summarizes. One persona per project."
    ),
)
async def get_persona(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Get the distilled L3 persona for a project.

    Args:
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown rendering of the persona (its distilled observations).
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        try:
            persona = await knowledge_client.get_persona()
        except ToolError:
            body = (
                "No L3 persona has been distilled yet. "
                "Run `distill_memory(level='L3')` (after L1) to generate it."
            )
            return add_project_metadata(body, active_project.name)

    md = persona.get("entity_metadata") or {}
    n_sources = len(md.get("source_entities") or [])
    lines = [
        f"# Persona — {persona.get('title')}",
        f"_{n_sources} stable facts aggregated_",
        "",
    ]
    content = persona.get("content") or ""
    if content:
        lines.append(content)
    return add_project_metadata("\n".join(lines), active_project.name)


@mcp.tool(
    description=(
        "Discover all observation categories in the project's L0 entities and identify "
        "which are already distillable vs which need to be added. Returns a report with: "
        "- All distinct categories found (with counts)\n"
        "- Categories already in the distillable set\n"
        "- Categories NOT yet distillable (candidates for inclusion).\n\n"
        "Use this before running a bulk distillation pass to understand what types of "
        "observations exist that may need to be added to the pipeline."
    ),
)
async def discover_categories(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Discover observation categories and identify which are distillable.

    Args:
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        A markdown report showing all observed categories, their counts,
        and which are already/distinctly in the distillable set.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=discover_categories project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        report = await knowledge_client.discover_categories()

    lines = [
        f"# Category Discovery — {active_project.name}",
        "",
        f"**Total distinct categories:** {report.get('total_categories', 'N/A')}",
        "",
    ]

    current_config = report.get("current_distillable_config", [])
    if current_config:
        lines.append("**Current distillable config:**")
        for cat in current_config:
            lines.append(f"- `{cat}`")
        lines.append("")

    distillable = report.get("distillable", [])
    unknown = report.get("unknown", [])
    counts = report.get("all_categories_with_counts", {})

    if distillable:
        lines.append(f"**Already distillable ({len(distillable)}):**")
        for cat in distillable:
            count = counts.get(cat, "?")
            lines.append(f"- ✓ `{cat}` (count: {count})")
        lines.append("")

    if unknown:
        lines.append(f"**Unknown / not yet distillable ({len(unknown)}):**")
        for cat in unknown:
            count = counts.get(cat, "?")
            lines.append(f"- ✗ `{cat}` (count: {count})")
        lines.append("")
        lines.append(
            f"Add with `add_categories(categories=['{unknown[0]}', ...])` or "
            f"`add_categories()` to auto-add all unknowns."
        )

    if not distillable and not unknown:
        lines.append("No observation categories found in the database.")

    return add_project_metadata("\n".join(lines), active_project.name)


@mcp.tool(
    description=(
        "Add one or more observation categories to the distillable set. When called "
        "without a list, auto-discovers all unknown (non-distillable) categories and "
        "adds them. This updates both the in-memory config and the distillation skill "
        "asset (if skills are enabled).\n\n"
        "Use discover_categories() first to see what needs adding."
    ),
)
async def add_categories(
    categories: Optional[list[str]] = None,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Add observation categories to the distillable set.

    Args:
        categories: List of category names to add. If omitted, auto-discovers all
                    unknown categories and adds them.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        A markdown summary showing added/skipped categories and new total count.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=add_categories project={active_project.name} "
            f"categories={categories}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        result = await knowledge_client.add_categories(categories)

    added = result.get("added", [])
    skipped = result.get("skipped", [])
    updated_skill = result.get("updated_skill", False)
    new_count = result.get("new_distillable_count")

    lines = ["# Category Update — " + active_project.name, ""]

    if added:
        lines.append(f"**Added ({len(added)}):**")
        for cat in added:
            lines.append(f"- `+ {cat}`")
        lines.append("")

    if skipped:
        lines.append(f"**Skipped (already present) ({len(skipped)}):**")
        for cat in skipped:
            lines.append(f"- `~ {cat}`")
        lines.append("")

    if not added and not skipped:
        lines.append("No categories to add.")

    if updated_skill:
        lines.append(
            "*Distillation skill asset was updated with new categories.*"
        )

    if new_count is not None:
        lines.append(f"**Total distillable categories now: {new_count}**")

    return add_project_metadata("\n".join(lines), active_project.name)