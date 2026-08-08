"""`memopad distill` CLI command (Tb L0-L3).

Drives the same code-only distillation engine the MCP tools and the automatic
create/watch hooks use, but from the command line. It talks to the in-process
API via the ASGI test client (same `get_client()` path as `memopad doctor`), so
no external server/key is required — the engine is pure in-app code.

Two modes:
  - default: run a distillation pass (`POST /knowledge/distill`) for the given
    levels and print per-level counts.
  - `--dry-run`: read-only inspection — print the current L1/L2/L3 counts
    (facts / scenarios / persona) WITHOUT triggering a new pass. Honest about
    what it is: it shows what is already distilled, not a speculative candidate
    preview (the engine only enumerates candidates by running).
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
import typer

from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.mcp.async_client import get_client
from memopad.mcp.clients import KnowledgeClient
from memopad.mcp.project_context import get_active_project

console = Console()

# Levels this command accepts, in pipeline order.
_LEVEL_ORDER = ("L1", "L2", "L3")


def _parse_levels(level: str) -> list[str]:
    """Parse a comma-separated level spec into a de-duplicated, ordered list.

    Args:
        level: e.g. "L1", "L1,L2,L3". Whitespace tolerant, case-insensitive.

    Returns:
        Ordered unique list of level tokens (e.g. ["L1", "L2", "L3"]).

    Raises:
        typer.BadParameter: if any token is not one of L1/L2/L3.
    """
    tokens = [t.strip().upper() for t in level.split(",") if t.strip()]
    if not tokens:
        raise typer.BadParameter("at least one level is required (L1/L2/L3)")
    invalid = [t for t in tokens if t not in _LEVEL_ORDER]
    if invalid:
        raise typer.BadParameter(
            f"invalid level(s): {', '.join(invalid)} (expected L1/L2/L3)"
        )
    # Preserve pipeline order, drop duplicates.
    return [lvl for i, lvl in enumerate(_LEVEL_ORDER) if lvl in set(tokens)]


async def _run_distill(
    project: Optional[str], level: str, max_memories: int
) -> None:
    """Trigger a distillation pass and print the per-level summary."""
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        console.print(
            f"[blue]Distilling[/blue] project='{active_project.name}' "
            f"level='{level}' max_memories={max_memories} ..."
        )
        data = await knowledge_client.distill_memory(level, max_memories=max_memories)

    console.print("[green]Distillation pass complete.[/green]")
    if "l1_facts" in data:
        console.print(f"  L1 facts:      {data['l1_facts']}")
    if "l2_scenarios" in data:
        console.print(f"  L2 scenarios:  {data['l2_scenarios']}")
    if "l3_persona" in data:
        console.print(f"  L3 persona:    {data['l3_persona']}")
    console.print(
        "[dim]Inspect with `memopad distill --dry-run` or the list_facts / "
        "list_scenarios / get_persona MCP tools.[/dim]"
    )


async def _run_dry_run(project: Optional[str]) -> None:
    """Read-only inspection: print current L1/L2/L3 counts without a pass."""
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        console.print(
            f"[blue]Dry run[/blue] (read-only) project='{active_project.name}' ..."
        )
        facts = await knowledge_client.list_facts(limit=1000)
        scenarios = await knowledge_client.list_scenarios(limit=1000)
        try:
            persona = await knowledge_client.get_persona()
        except ToolError:
            persona = None

    console.print(f"  L1 facts:      {len(facts)}")
    console.print(f"  L2 scenarios:  {len(scenarios)}")
    if persona is not None:
        md = persona.get("entity_metadata") or {}
        n_sources = len(md.get("source_entities") or [])
        console.print(f"  L3 persona:    1 ({n_sources} stable facts aggregated)")
    else:
        console.print("  L3 persona:    0 (none yet — run `memopad distill --level L3`)")
    console.print("[dim]No pass was run; this is the current distilled state.[/dim]")


@app.command()
def distill(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Project name. Optional — resolves the default project when omitted.",
    ),
    level: str = typer.Option(
        "L1",
        "--level",
        "-l",
        help="Comma-separated levels to run: L1, L2, L3, or any combination "
        "(e.g. 'L1,L2,L3'). Default 'L1'.",
    ),
    max_memories: int = typer.Option(
        50,
        "--max-memories",
        "-m",
        help="Max L0 entities to scan per L1 pass (1-1000). Default 50.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Read-only: print the current L1/L2/L3 counts without running a pass.",
    ),
) -> None:
    """Run (or inspect) the L0->L1->L2->L3 distillation pipeline.

    Distillation also runs automatically on every write (create path + file
    sync). This command is for on-demand passes and inspection. Pure in-app
    code — no external model/API/key.
    """
    try:
        if dry_run:
            run_with_cleanup(_run_dry_run(project))
            return

        levels = _parse_levels(level)
        if max_memories < 1 or max_memories > 1000:
            raise typer.BadParameter("--max-memories must be between 1 and 1000")
        level_spec = ",".join(levels)
        run_with_cleanup(_run_distill(project, level_spec, max_memories))
    except (ToolError, ValueError) as e:
        console.print(f"[red]Distill failed: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Distill failed: {e}")
        typer.echo(f"Distill failed: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover