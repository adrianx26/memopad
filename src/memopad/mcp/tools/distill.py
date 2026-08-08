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
        "Pure in-app code (no external model/API/key)."
    ),
)
async def distill_memory(
    level: str = "L1",
    project: Optional[str] = None,
    max_memories: int = 50,
    context: Context | None = None,
) -> str:
    """Trigger a distillation pass for the given levels.

    Args:
        level: Comma-separated levels to run: "L1", "L2", "L3", or any combination
            (e.g. "L1,L2,L3"). Default "L1".
        project: Project name. Optional — server resolves the default.
        max_memories: Max L0 entities to scan per L1 pass (1–1000). Default 50.
        context: Optional FastMCP context.

    Returns:
        A markdown summary with per-level counts.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=distill_memory level={level} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.distill_memory(level, max_memories=max_memories)

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