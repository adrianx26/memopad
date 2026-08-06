"""Skill asset tools for the MemoPad MCP server (Tb G1).

A Skill is reusable, versioned expertise — "what the agent has learned to do
well" — modelled as an entity with `entity_type = "skill"`, `skill_version` /
`skill_status` metadata, and `[trigger]` / `[step]` / `[validation]` (plus
optional `[when]` / `[do]` / `[don't]`) observations.

Tools:
- `create_skill` — write a new skill (v1, draft) as a markdown file.
- `get_skill`    — read a skill with its trigger/steps/validation grouped.
- `list_skills`  — list skills, optionally filtered by status.
- `validate_skill` — structurally check the Tb triple and promote to
  `validated` when complete.

Create/get compose the generic entity CRUD; list/validate use the skill-specific
endpoints. All skill-specific behaviour is gated behind `skills_enabled` at the
service boundary, so with the flag off these still work as ordinary note
operations (no semantic validation or boost).
"""

from typing import List, Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp
from memopad.schemas.base import Entity
from memopad.services.skill_service import (
    STATUS_DRAFT,
    VALID_STATUSES,
    build_skill_body,
    build_skill_detail,
    render_skill_detail,
)


@mcp.tool(
    description=(
        "Create a reusable, versioned Skill asset (entity_type=skill). "
        "A skill captures expertise with a trigger (when to apply it), numbered "
        "steps (how to execute), and a validation rule (how to verify success), "
        "plus optional when/do/don't guard-rails. Created as a v1 draft markdown "
        "file; call validate_skill to promote it to 'validated'. Use for any "
        "repeatable procedure the agent has learned to do well."
    ),
)
async def create_skill(
    title: str,
    trigger: str,
    steps: List[str],
    validation: str,
    project: Optional[str] = None,
    directory: str = "skills",
    skill_status: str = STATUS_DRAFT,
    source_entities: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    when: Optional[str] = None,
    do: Optional[str] = None,
    dont: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Create a new skill (v1, draft by default).

    Args:
        title: Skill title (e.g. "Reset DB safely").
        trigger: When to invoke this skill (the [trigger] observation).
        steps: Ordered execution steps (each becomes a [step] observation).
        validation: How to verify success (the [validation] observation).
        project: Project name. Optional — server resolves the default.
        directory: Directory for the skill file. Default "skills".
        skill_status: Lifecycle status: draft | validated | deprecated. Default draft.
        source_entities: Optional provenance — memory:// URIs of the incidents/learnings
            this skill was distilled from (drill_down traces these back to L0).
        tags: Optional tag list.
        when / do / dont: Optional guard-rail observations.
        context: Optional FastMCP context.

    Returns:
        Markdown summary of the created skill.
    """
    if skill_status not in VALID_STATUSES:
        return (
            f"# Error\n\n`skill_status` must be one of {sorted(VALID_STATUSES)}, "
            f"got '{skill_status}'."
        )
    if not steps:
        return "# Error\n\n`steps` must contain at least one step."

    body = build_skill_body(
        trigger=trigger, steps=steps, validation=validation, when=when, do=do, dont=dont
    )

    metadata: dict = {"skill_version": 1, "skill_status": skill_status}
    if source_entities:
        metadata["source_entities"] = source_entities
    if tags:
        metadata["tags"] = tags

    entity = Entity(
        title=title,
        directory=directory,
        entity_type="skill",
        content_type="text/markdown",
        content=body,
        entity_metadata=metadata,
    )

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=create_skill title={title} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        action = "Created"
        try:
            result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
        except Exception as e:
            if (
                "409" in str(e)
                or "conflict" in str(e).lower()
                or "already exists" in str(e).lower()
            ):
                entity_id = await knowledge_client.resolve_entity(entity.permalink)
                result = await knowledge_client.update_entity(
                    entity_id, entity.model_dump(), fast=False
                )
                action = "Updated"
            else:
                raise

    summary = [
        f"# {action} skill",
        f"title: {result.title}",
        f"permalink: `{result.permalink}`" if result.permalink else "permalink: _(none)_",
        f"file: `{result.file_path}`",
        f"version: 1  |  status: `{skill_status}`",
        f"steps: {len(steps)}  |  trigger: yes  |  validation: yes",
    ]
    if source_entities:
        summary.append(f"sources: {len(source_entities)} (drill_down to trace to L0)")
    body_md = "\n".join(summary)
    return add_project_metadata(body_md, active_project.name)


@mcp.tool(
    description=(
        "Read a skill with its trigger/steps/validation grouped into sections. "
        "Accepts a permalink, title, or memory:// URL. Use this (not read_note) "
        "when you want the skill's structure rather than raw observations."
    ),
)
async def get_skill(
    identifier: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Read a skill as a structured Markdown document.

    Args:
        identifier: Permalink, title, or memory:// URL of the skill.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown with Trigger / Steps / Validation / When / Do / Don't sections.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=get_skill identifier={identifier} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        try:
            entity_id = await knowledge_client.resolve_entity(identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve identifier '{identifier}': {e}"

        entity = await knowledge_client.get_entity(entity_id)

    if entity.entity_type != "skill":
        return (
            f"# Error\n\n'{identifier}' is not a skill "
            f"(entity_type='{entity.entity_type}'). Use read_note for other entity types."
        )

    detail = build_skill_detail(entity, entity.observations)
    return add_project_metadata(render_skill_detail(detail), active_project.name)


@mcp.tool(
    description=(
        "List skill assets, optionally filtered by status (draft | validated | "
        "deprecated). Returns a compact summary (title, permalink, version, "
        "status) per skill. Use get_skill for full detail on a chosen one."
    ),
)
async def list_skills(
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    context: Context | None = None,
) -> str:
    """List skills, optionally filtered by status.

    Args:
        project: Project name. Optional — server resolves the default.
        status: Optional filter: draft | validated | deprecated.
        limit: Max skills to return. Default 50.
        context: Optional FastMCP context.

    Returns:
        Markdown table of skills.
    """
    if status and status not in VALID_STATUSES:
        return (
            f"# Error\n\n`status` must be one of {sorted(VALID_STATUSES)}, "
            f"got '{status}'."
        )

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=list_skills status={status} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        data = await knowledge_client.list_skills(status=status, limit=limit)

    skills = data.get("skills", []) or []
    if not skills:
        line = f"# Skills in {active_project.name}\n\n_No skills found"
        line += f" with status='{status}'._" if status else "._"
        return add_project_metadata(line, active_project.name)

    header = f"# Skills in {active_project.name} ({len(skills)})"
    table = ["| Title | Permalink | Version | Status |", "|---|---|---|---|"]
    for s in skills:
        table.append(
            f"| {s['title']} | `{s['permalink']}` | {s['skill_version']} | `{s['skill_status']}` |"
        )
    return add_project_metadata(header + "\n\n" + "\n".join(table), active_project.name)


@mcp.tool(
    description=(
        "Validate a skill and promote it to 'validated'. Checks the Tb "
        "triple is present (>= 1 trigger, >= 1 step, >= 1 validation). When "
        "complete, sets skill_status=validated in the file frontmatter. When "
        "incomplete, reports the missing categories without changing status. "
        "LLM verification of step/trigger coverage is deferred — this is the "
        "structural gate."
    ),
)
async def validate_skill(
    identifier: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Structurally validate a skill and promote it to `validated` if complete.

    Args:
        identifier: Permalink, title, or memory:// URL of the skill.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown report: ok/missing categories and the resulting status.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=validate_skill identifier={identifier} project={active_project.name}"
        )

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        try:
            entity_id = await knowledge_client.resolve_entity(identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve identifier '{identifier}': {e}"

        result = await knowledge_client.validate_skill(entity_id)

    ok = result.get("ok", False)
    missing = result.get("missing", []) or []
    present = result.get("present", []) or []
    final_status = result.get("skill_status", "?")

    lines = [
        f"# Validation: {identifier}",
        f"project: {active_project.name}",
        f"resulting status: `{final_status}`",
    ]
    if ok:
        lines.append(
            "✅ **Structurally valid** — has >= 1 trigger, >= 1 step, >= 1 validation. "
            "Status set to `validated`."
        )
    else:
        lines.append(
            f"❌ **Incomplete** — missing: {', '.join(f'[{m}]' for m in missing)}. "
            f"Present: {', '.join(f'[{p}]' for p in present) or 'none'}."
        )
    lines.append(
        "_LLM verification (do the steps cover the trigger? does the validation "
        "rule hold?) is deferred — structural completeness is the precondition._"
    )
    return add_project_metadata("\n".join(lines), active_project.name)