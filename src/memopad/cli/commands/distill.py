"""`memopad distill` CLI command (Tb L0-L3).

Drives the same code-only distillation engine the MCP tools and the automatic
create/watch hooks use, but from the command line. It talks to the in-process
API via the ASGI test client (same `get_client()` path as `memopad doctor`), so
no external server/key is required — the engine is pure in-app code.

Subcommands:
  - run: run a distillation pass for specified levels.
  - dry-run: read-only inspection of current L1/L2/L3 counts.
  - discover-categories: discover observation categories and identify distillable vs unknown.
  - add-categories: add one or more observation categories to the distillable set.

The default invocation (no subcommand) runs a default L1 pass.
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
import typer

from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.mcp.async_client import get_client
from memopad.mcp.clients.knowledge import KnowledgeClient
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
    project: Optional[str], level: str, max_memories: int, bulk: bool
) -> None:
    """Trigger a distillation pass and print the per-level summary."""
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        console.print(
            f"[blue]Distilling[/blue] project='{active_project.name}' "
            f"level='{level}' max_memories={max_memories} bulk={bulk} ..."
        )
        data = await knowledge_client.distill_memory(
            level, max_memories=max_memories, bulk=bulk
        )

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


async def _run_discover_categories(project: Optional[str]) -> None:
    """Discover observation categories and print a report."""
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        console.print(
            f"[blue]Discovering categories[/blue] "
            f"project='{active_project.name}' ..."
        )
        report = await knowledge_client.discover_categories()

    console.print("[green]Category discovery complete.[/green]")
    console.print(f"\n  Total distinct categories: {report.get('total_categories', 'N/A')}")
    
    distillable = report.get("distillable", [])
    unknown = report.get("unknown", [])
    current_config = report.get("current_distillable_config", [])
    
    console.print(f"\n  [dim]Current distillable config:[/dim]")
    for cat in current_config:
        console.print(f"    - {cat}")
    
    if distillable:
        console.print(f"\n  [green]Already distillable ({len(distillable)}):[/green]")
        for cat in distillable:
            console.print(f"    ✓ {cat}")
    
    if unknown:
        console.print(f"\n  [yellow]Unknown / not yet distillable ({len(unknown)}):[/yellow]")
        for cat in unknown:
            count = report.get("all_categories_with_counts", {}).get(cat, "?")
            console.print(f"    ✗ {cat} (count: {count})")
        console.print(
            f"\n  [dim]Add with: memopad distill add-categories --categories cat1,cat2[/dim]"
        )
    
    if not unknown and not distillable:
        console.print("\n  [dim]No observation categories found in the database.[/dim]")


async def _run_add_categories(
    project: Optional[str], categories: List[str], auto: bool
) -> None:
    """Add observation categories to the distillable set."""
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        
        if auto:
            console.print(
                f"[blue]Auto-discovering and adding unknown categories[/blue] "
                f"project='{active_project.name}' ..."
            )
            result = await knowledge_client.add_categories()
        else:
            console.print(
                f"[blue]Adding {len(categories)} category/categories[/blue] "
                f"project='{active_project.name}' categories={categories} ..."
            )
            result = await knowledge_client.add_categories(categories)

    added = result.get("added", [])
    skipped = result.get("skipped", [])
    
    console.print("[green]Category update complete.[/green]")
    if added:
        console.print(f"\n  [green]Added ({len(added)}):[/green]")
        for cat in added:
            console.print(f"    + {cat}")
    if skipped:
        console.print(f"\n  [yellow]Skipped (already present) ({len(skipped)}):[/yellow]")
        for cat in skipped:
            console.print(f"    ~ {cat}")
    
    updated_skill = result.get("updated_skill", False)
    new_count = result.get("new_distillable_count")
    if updated_skill:
        console.print("\n  [dim]Distillation skill asset updated with new categories.[/dim]")
    if new_count is not None:
        console.print(f"  [dim]Total distillable categories now: {new_count}[/dim]")


# --- Distill subcommand group -----------------------------------------------

distill_app = typer.Typer(rich_markup_mode="rich")
app.add_typer(distill_app, name="distill")


@distill_app.callback(invoke_without_command=True)
def cmd_distill(
    ctx: typer.Context,
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
    bulk: bool = typer.Option(
        False,
        "--bulk",
        help="Process ALL existing L0 entities (cold-start / backfill mode). "
        "Default False = incremental (only updated-since-watermark).",
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

    The default invocation (no subcommand) runs a distillation pass; use the
    subcommands for category discovery/management:

      - discover-categories: discover observation categories and identify distillable vs unknown.
      - add-categories: add one or more observation categories to the distillable set.

    Use --bulk for a one-time cold-start that processes ALL existing L0 entities.
    After bulk mode completes, the watermark is set so future incremental passes
    only process new/changed entities.
    """
    # A subcommand (discover-categories / add-categories) handles its own args;
    # only run the distill pass when invoked bare.
    if ctx.invoked_subcommand is not None:
        return
    try:
        if dry_run:
            run_with_cleanup(_run_dry_run(project))
            return

        levels = _parse_levels(level)
        if max_memories < 1 or max_memories > 1000:
            raise typer.BadParameter("--max-memories must be between 1 and 1000")
        level_spec = ",".join(levels)
        run_with_cleanup(_run_distill(project, level_spec, max_memories, bulk))
    except (ToolError, ValueError) as e:
        console.print(f"[red]Distill failed: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Distill failed: {e}")
        typer.echo(f"Distill failed: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover


@distill_app.command("discover-categories")
def cmd_discover_categories(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Project name. Optional — resolves the default project when omitted.",
    ),
) -> None:
    """Discover observation categories and identify which are distillable.

    Shows all distinct observation categories found in L0 entities, along with
    counts. Identifies which categories are already in the distillable set and
    which are not (candidates for inclusion).
    
    Use this before running a bulk distillation pass to understand what types
    of observations exist in your database that may need to be added to the
    distillation pipeline.
    """
    try:
        run_with_cleanup(_run_discover_categories(project))
    except (ToolError, ValueError) as e:
        console.print(f"[red]Discover categories failed: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Discover categories failed: {e}")
        typer.echo(f"Discover categories failed: {e}", err=True)
        raise typer.Exit(code=1)


@distill_app.command("add-categories")
def cmd_add_categories(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Project name. Optional — resolves the default project when omitted.",
    ),
    categories: List[str] = typer.Option(
        None,
        "--categories",
        "-c",
        help="Comma-separated list of category names to add (e.g. 'rule,definition'). "
        "If omitted with --auto, auto-discovers all unknown categories.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto-discover and add all non-distillable categories.",
    ),
) -> None:
    """Add observation categories to the distillable set.

    When called with --categories, adds only those specific categories.
    When called with --auto (or no arguments), auto-discovers all unknown
    categories from L0 entities and adds them.
    
    This updates both the in-memory config and the distillation skill asset
    (if skills are enabled) so future passes include these categories.
    """
    try:
        if categories is not None and len(categories) == 0:
            raise typer.BadParameter("at least one category name is required")
        
        run_with_cleanup(_run_add_categories(project, categories or [], auto))
    except (ToolError, ValueError) as e:
        console.print(f"[red]Add categories failed: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Add categories failed: {e}")
        typer.echo(f"Add categories failed: {e}", err=True)
        raise typer.Exit(code=1)
