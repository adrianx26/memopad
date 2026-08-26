"""`memopad embeddings` CLI command — safe embedding backfill.

Backfills the optional embedding store (used by hybrid semantic search) for
existing entities WITHOUT touching observations. Only the ``embedding`` table is
written; entities and observations are read-only here. This is the safe,
built-in replacement for the external ``embedding-backfill.py`` script users had
to write because ``memopad reindex --embeddings`` was never shipped (Bug 9 in the
2026-08-26 debugging report). Insert-missing semantics by default; ``--force``
re-embeds everything.

Subcommands:
  - backfill: embed entities that don't yet have a vector.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from rich.console import Console
import typer

from memopad import db
from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.config import ConfigManager

console = Console()

embeddings_app = typer.Typer(rich_markup_mode="rich")
app.add_typer(embeddings_app, name="embeddings")


def _entity_text(entity, observations) -> str:
    """Build the text to embed for an entity from its title + observations.

    Read-only: never mutates the entity or observations. We embed the title plus
    each observation's ``category: content`` so semantic search matches the
    entity's actual knowledge, not just its name.
    """
    parts = [entity.title or ""]
    for obs in observations or []:
        cat = getattr(obs, "category", None) or "note"
        content = getattr(obs, "content", None) or ""
        if content:
            parts.append(f"{cat}: {content}")
    return "\n".join(p for p in parts if p).strip() or (entity.title or "")


async def _backfill_project(
    session_maker,
    project,
    force: bool,
    limit: Optional[int],
    batch_size: int,
) -> dict:
    """Backfill embeddings for one project. Returns a stats dict.

    Only writes the ``embedding`` table. Entities and observations are read-only.
    """
    from memopad.services.embedding_service import EmbeddingService, is_enabled

    if not is_enabled():
        return {"skipped_disabled": True}

    embedding_service = EmbeddingService.maybe_create(session_maker, project.id)
    if not embedding_service:
        return {"skipped_disabled": True}

    await embedding_service.init_store()

    from memopad.repository import EntityRepository, ObservationRepository

    entity_repo = EntityRepository(session_maker, project.id)
    obs_repo = ObservationRepository(session_maker, project.id)

    entities = await entity_repo.find_all(limit=limit) if limit else await entity_repo.find_all()
    total = len(entities)
    if total == 0:
        return {"total": 0, "backfilled": 0, "already": 0}

    already = set() if force else await embedding_service.existing_ids()
    missing = [e for e in entities if e.id not in already]
    if not missing:
        return {"total": total, "backfilled": 0, "already": total}

    # Batch-fetch observations for the entities we're about to (re-)embed.
    obs_map = await obs_repo.find_by_entities([e.id for e in missing])

    items = [(e.id, _entity_text(e, obs_map.get(e.id, []))) for e in missing]

    written = 0
    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        written += await embedding_service.upsert_many(chunk)

    return {
        "total": total,
        "backfilled": written,
        "already": total - len(missing),
        "re_embedded": total if force else 0,
    }


async def _run_backfill(
    project: Optional[str],
    force: bool,
    limit: Optional[int],
    batch_size: int,
) -> None:
    """Drive the backfill across the active project (or all projects)."""
    config_manager = ConfigManager()
    app_config = config_manager.config

    try:
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        from memopad.repository import ProjectRepository

        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        if project:
            projects = [p for p in projects if p.name == project or p.path == project]
            if not projects:
                console.print(
                    f"[red]Error:[/red] No project named '{project}'. "
                    f"Use `memopad project list` to see configured projects."
                )
                raise typer.Exit(code=1)

        for proj in projects:
            console.print(f"[blue]Backfilling embeddings[/blue] for project='{proj.name}' ...")
            try:
                stats = await _backfill_project(
                    session_maker, proj, force=force, limit=limit, batch_size=batch_size
                )
            except Exception as e:  # pragma: no cover
                logger.exception(f"Embedding backfill failed for project {proj.name}")
                console.print(f"  [red]Failed:[/red] {e}")
                continue

            if stats.get("skipped_disabled"):
                console.print(
                    "  [yellow]Skipped:[/yellow] embeddings disabled. "
                    f"Set MEMOPAD_EMBEDDINGS_ENABLED=true and install 'memopad[embeddings]'."
                )
                continue

            console.print(
                f"  [green]Done:[/green] total={stats['total']} "
                f"backfilled={stats['backfilled']} already_present={stats['already']}"
                + (" (re-embedded all)" if force else "")
            )
    finally:
        await db.shutdown_db()


@embeddings_app.command("backfill")
def cmd_backfill(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Project name or path. Omit to backfill all active projects.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-embed ALL entities, not just those missing a vector.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Max entities to process per project (debug). Omit for all.",
    ),
    batch_size: int = typer.Option(
        128,
        "--batch-size",
        help="Entities embedded per provider call (default 128).",
    ),
) -> None:
    """Backfill the embedding store for existing entities (insert-missing).

    Safe by construction: only the `embedding` table is written. Entities and
    observations are never modified. Requires MEMOPAD_EMBEDDINGS_ENABLED=true and
    the optional `embeddings` extra (fastembed).
    """
    run_with_cleanup(_run_backfill(project, force, limit, batch_size))