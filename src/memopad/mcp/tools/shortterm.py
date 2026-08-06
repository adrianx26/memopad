"""MCP tools for the in-task short-term context layers (Tb G6).

These tools expose the session-scoped 3-layer stack (refs -> steps -> Mermaid
canvas) implemented in ``shortterm_context.py``. The whole feature is gated by
``shortterm_enabled`` (default off): when off, every tool fails fast with a clear
message instead of silently doing nothing.

Session state is filesystem-only, under ``<data_dir>/sessions/<session_id>/`` —
no HTTP, no DB, no L0–L3 coupling. ``session_id`` is caller-supplied (the agent
knows its own session); it is validated as a plain filename so it can never
escape the sessions root.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from loguru import logger
from fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from memopad.config import ConfigManager, MemoPadConfig
from memopad.mcp.server import mcp
from memopad.services.shortterm_context import (
    LEVEL_AGGRESSIVE,
    LEVEL_MILD,
    LEVEL_NONE,
    ShortTermConfig,
    ShortTermContext,
    ShortTermError,
    safe_ref_name,
)


def _disabled_error() -> ToolError:
    return ToolError(
        "Short-term context layering is disabled. Set "
        "'shortterm_enabled=true' (and a non-zero 'shortterm_context_token_budget') "
        "in the MemoPad config to enable the in-task session context stack."
    )


def _load() -> tuple[ShortTermConfig, MemoPadConfig]:
    """Load config and project the G6 policy, or raise if the feature is off.

    Returns (shortterm_config, app_config). Shared by every tool so the gate is
    in one place.
    """
    app_config = ConfigManager().load_config()
    if not app_config.shortterm_enabled:
        raise _disabled_error()
    cfg = ShortTermConfig.from_app_config(app_config)
    return cfg, app_config


def _sessions_root(app_config) -> Path:
    # `data_dir_path` is a @property on MemoPadConfig (returns a Path), not a
    # method — calling it with parens raises TypeError and breaks every G6 tool
    # the moment `shortterm_enabled` is on. doctor.py uses the parens-free form;
    # this must match.
    return Path(app_config.data_dir_path) / "sessions"


def _service(session_id: str) -> ShortTermContext:
    cfg, app_config = _load()
    safe_ref_name(session_id)  # reject traversal / path-bearing ids up front
    session_dir = _sessions_root(app_config) / session_id
    return ShortTermContext(session_dir, cfg)


@mcp.tool(
    description=(
        "Record a raw tool output in the session's short-term context (the refs "
        "layer — ground truth for drill-down). Returns the resulting offload level."
    ),
)
async def add_session_ref(
    session_id: str,
    name: str,
    content: str,
    context: Context | None = None,
) -> str:
    """Append a raw ref to ``sessions/<session_id>/refs/<name>.md`` and evaluate offload.

    Args:
        session_id: The session identifier (plain filename; no path separators).
        name: Ref name (plain filename; overwrites if the same name is re-added).
        content: The raw tool output to store as ground truth.
        context: Optional FastMCP context.

    Returns:
        A one-line summary with the new offload level.
    """
    try:
        st = _service(session_id)
        st.add_ref(name, content)
        level = st.maybe_offload()
    except ShortTermError as e:
        raise ToolError(str(e)) from e
    logger.info(f"session_ref {session_id}/{name}: offload={level}")
    return f"Stored ref '{name}' in session '{session_id}'. Offload level: {level}."


@mcp.tool(
    description=(
        "Record a summarized tool-call step in the session's short-term context "
        "(the steps layer). Each step becomes a Mermaid node 's<index>' for drill-down."
    ),
)
async def add_session_step(
    session_id: str,
    tool: str,
    summary: str,
    ref_name: Optional[str] = None,
    timestamp: str = "",
    context: Context | None = None,
) -> str:
    """Append a step to ``sessions/<session_id>/steps.jsonl`` and evaluate offload.

    Args:
        session_id: The session identifier.
        tool: Name of the tool that produced this step.
        summary: One-line summary of what the step did (caller/LLM-supplied).
        ref_name: Optional backing raw ref name for drill-down.
        timestamp: Optional caller-supplied timestamp (kept off the engine clock).
        context: Optional FastMCP context.

    Returns:
        The assigned node id (e.g. 's3') and the resulting offload level.
    """
    try:
        st = _service(session_id)
        record = st.add_step(tool, summary, ref_name=ref_name, timestamp=timestamp)
        level = st.maybe_offload()
    except ShortTermError as e:
        raise ToolError(str(e)) from e
    logger.info(f"session_step {session_id}/{record.node_id()}: offload={level}")
    return (
        f"Recorded step {record.node_id()} ({tool}) in session '{session_id}'. "
        f"Offload level: {level}."
    )


@mcp.tool(
    description=(
        "Get the current in-task session context to inject into the agent. As the "
        "session grows, layers offload: refs -> steps -> condensed Mermaid canvas."
    ),
)
async def get_session_context(
    session_id: str,
    context: Context | None = None,
) -> str:
    """Return the layers to inject right now, per the offload policy.

    - none: refs + steps
    - mild: steps only (refs offloaded to disk, still drill-downable)
    - aggressive: the condensed Mermaid canvas (steps drill-downable)

    Args:
        session_id: The session identifier.
        context: Optional FastMCP context.

    Returns:
        A markdown summary of the injected layers.
    """
    try:
        st = _service(session_id)
        inj = st.build_injection()
    except ShortTermError as e:
        raise ToolError(str(e)) from e

    level = inj["offload_level"]
    lines: List[str] = [f"# Session context ({level})", ""]

    if level == LEVEL_AGGRESSIVE:
        canvas = inj.get("canvas", "")
        lines.append("Condensed canvas (top layer):")
        lines.append("```mermaid")
        lines.append(canvas)
        lines.append("```")
        lines.append(
            "\nSteps are offloaded from the active context but reachable via "
            "`drill_down_session(node_id='s<index>')`."
        )
    else:
        steps = inj.get("steps", [])
        lines.append(f"Steps ({len(steps)}):")
        for s in steps:
            ref = f" → ref:{s.ref_name}" if s.ref_name else ""
            lines.append(f"- `s{s.index}` **{s.tool}**: {s.summary}{ref}")
        if level == LEVEL_NONE:
            refs = inj.get("refs", [])
            lines.append(f"\nRaw refs ({len(refs)}):")
            for r in refs:
                lines.append(f"- `{r.name}` ({r.token_estimate} tokens)")
        else:  # mild
            lines.append(
                "\nRaw refs are offloaded from the active context but reachable via "
                "`drill_down_session(node_id='s<index>')`."
            )
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Drill down into a session context node ('s<index>') to recover the source "
        "step and its raw ref — the 'top symbol -> raw text' descent."
    ),
)
async def drill_down_session(
    session_id: str,
    node_id: str,
    context: Context | None = None,
) -> str:
    """Resolve a Mermaid node id to its source step and (if any) raw ref content.

    Args:
        session_id: The session identifier.
        node_id: The canvas node id, e.g. 's3'.
        context: Optional FastMCP context.

    Returns:
        A markdown view of the step plus its backing raw ref.
    """
    try:
        st = _service(session_id)
        result = st.drill_down(node_id)
    except ShortTermError as e:
        raise ToolError(str(e)) from e

    step = result["step"]
    lines: List[str] = [
        f"# Drill-down: {node_id}",
        f"**Tool:** {step.tool}",
        f"**Summary:** {step.summary}",
        f"**Index:** {step.index}",
    ]
    if "ref_content" in result:
        ref = result.get("ref_content")
        if ref is None:
            lines.append(f"\n**Ref:** `{result.get('ref_missing')}` (missing — never written)")
        else:
            lines.append(f"\n**Ref content** (`{step.ref_name}`):")
            lines.append("```markdown")
            lines.append(ref)
            lines.append("```")
    else:
        lines.append("\n*This step has no backing raw ref.*")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Finalize a session: return the stable steps eligible for promotion to an "
        "L2 scenario, and optionally clear the session directory."
    ),
)
async def finalize_session(
    session_id: str,
    clear: bool = False,
    context: Context | None = None,
) -> str:
    """End-of-session hook: surface stable steps (the ScenarioBuilder seam) and optionally clean up.

    Args:
        session_id: The session identifier.
        clear: If True, remove the session directory after reading the steps.
        context: Optional FastMCP context.

    Returns:
        A markdown list of the stable steps.
    """
    try:
        st = _service(session_id)
        steps = st.stable_steps()
        if clear:
            st.clear()
    except ShortTermError as e:
        raise ToolError(str(e)) from e

    lines: List[str] = [f"# Finalized session '{session_id}' ({len(steps)} stable steps)", ""]
    for s in steps:
        ref = f" → ref:{s.ref_name}" if s.ref_name else ""
        lines.append(f"- `s{s.index}` **{s.tool}**: {s.summary}{ref}")
    lines.append(
        "\nThese steps are the deterministic input for a future ScenarioBuilder "
        "that mints an L2 scenario entity (levels plan, Faza 4)."
    )
    if clear:
        lines.append("\n(Session directory cleared.)")
    return "\n".join(lines)