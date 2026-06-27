"""Database management commands."""

import os
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from sqlalchemy.exc import OperationalError

from memopad import db
from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.config import ConfigManager
from memopad.repository import ProjectRepository
from memopad.services.embedding_service import is_enabled as embeddings_enabled
from memopad.services.initialization import reconcile_projects_with_config
from memopad.sync.sync_service import get_sync_service

console = Console()


async def _reindex_projects(app_config):
    """Reindex all projects in a single async context.

    This ensures all database operations use the same event loop,
    and proper cleanup happens when the function completes.
    """
    try:
        await reconcile_projects_with_config(app_config)

        # Get database session (migrations already run if needed)
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        for project in projects:
            console.print(f"  Indexing [cyan]{project.name}[/cyan]...")
            logger.info(f"Starting sync for project: {project.name}")
            sync_service = await get_sync_service(project)
            sync_dir = Path(project.path)
            await sync_service.sync(sync_dir, project_name=project.name)
            logger.info(f"Sync completed for project: {project.name}")
    finally:
        # Clean up database connections before event loop closes
        await db.shutdown_db()


async def _reindex_all_projects(app_config, embeddings: bool = False, batch_size: int = 128):
    """Rebuild the search index (and optionally embeddings) for every project.

    Uses SearchService.reindex_all() rather than a filesystem sync so we rebuild
    the index from what's already in the database without touching note files.
    When `embeddings` is true the env var is already set by the caller, so the
    injected EmbeddingService upserts a vector for each indexed note. `batch_size`
    controls the embedding backfill chunk size (one model call per chunk).
    """
    try:
        await reconcile_projects_with_config(app_config)

        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        for project in projects:
            label = f"{project.name} (embeddings)" if embeddings else project.name
            console.print(f"  Indexing [cyan]{label}[/cyan]...")
            logger.info(f"Starting reindex for project: {project.name}")
            # get_sync_service wires session_maker + project_id into SearchService,
            # so reindex_all() will backfill embeddings when enabled.
            sync_service = await get_sync_service(project)
            await sync_service.search_service.reindex_all(batch_size=batch_size)
            logger.info(f"Reindex completed for project: {project.name}")
    finally:
        await db.shutdown_db()


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    console.print(
        "[yellow]Note:[/yellow] This only deletes the index database. "
        "Your markdown note files will not be affected.\n"
        "Use [green]bm reset --reindex[/green] to automatically rebuild the index afterward."
    )
    if typer.confirm("Reset the database index?"):
        logger.info("Resetting database...")
        config_manager = ConfigManager()
        app_config = config_manager.config
        # Get database path
        db_path = app_config.app_database_path

        # Delete the database file and WAL files if they exist
        for suffix in ["", "-shm", "-wal"]:
            path = db_path.parent / f"{db_path.name}{suffix}"
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Deleted: {path}")
                except OSError as e:
                    console.print(
                        f"[red]Error:[/red] Cannot delete {path.name}: {e}\n"
                        "The database may be in use by another process (e.g., MCP server).\n"
                        "Please close Claude Desktop or any other Basic Memory clients and try again."
                    )
                    raise typer.Exit(1)

        # Create a new empty database (preserves project configuration)
        try:
            run_with_cleanup(db.run_migrations(app_config))
        except OperationalError as e:
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                console.print(
                    "[red]Error:[/red] Cannot access database. "
                    "It may be in use by another process (e.g., MCP server).\n"
                    "Please close Claude Desktop or any other Basic Memory clients and try again."
                )
                raise typer.Exit(1)
            raise
        console.print("[green]Database reset complete[/green]")

        if reindex:
            projects = list(app_config.projects)
            if not projects:
                console.print("[yellow]No projects configured. Skipping reindex.[/yellow]")
            else:
                console.print(f"Rebuilding search index for {len(projects)} project(s)...")
                # Note: _reindex_projects has its own cleanup, but run_with_cleanup
                # ensures db.shutdown_db() is called even if _reindex_projects changes
                run_with_cleanup(_reindex_projects(app_config))
                console.print("[green]Reindex complete[/green]")


@app.command()
def reindex(
    embeddings: bool = typer.Option(
        False,
        "--embeddings",
        help=(
            "Also backfill semantic embeddings for every note. "
            "Requires the optional extra: pip install 'memopad[embeddings]'."
        ),
    ),
    batch_size: int = typer.Option(
        128,
        "--batch-size",
        help=(
            "Number of items embedded per model call during --embeddings backfill. "
            "Larger = fewer model calls (faster), more memory."
        ),
    ),
):  # pragma: no cover
    """Rebuild the search index from the database for all projects.

    Unlike `reset --reindex` (which rebuilds after dropping the DB), this
    command leaves the database in place and just repopulates the search index
    from existing entity rows.

    Pass `--embeddings` to additionally backfill semantic vectors for every
    note. This is the command `semantic_search` points users at when
    embeddings are enabled but not yet populated.
    """
    config_manager = ConfigManager()
    app_config = config_manager.config

    if embeddings:
        # Force-enable embeddings for this process so the backfill writes vectors
        # even if the user hasn't set the env var globally.
        os.environ["MEMOPAD_EMBEDDINGS_ENABLED"] = "true"
        if not embeddings_enabled():
            console.print(
                "[red]--embeddings requires the optional extra:[/red]\n"
                "    pip install 'memopad[embeddings]'\n"
                "Then re-run `memopad reindex --embeddings`."
            )
            raise typer.Exit(1)
        console.print(
            "[yellow]Backfilling embeddings.[/yellow] The model loads on first "
            "use (one-time download, ~30MB). Subsequent reindexes reuse the "
            "cached model."
        )

    projects = list(app_config.projects)
    if not projects:
        console.print("[yellow]No projects configured. Nothing to reindex.[/yellow]")
        raise typer.Exit(0)

    label = "Reindexing (with embeddings)" if embeddings else "Reindexing"
    console.print(f"{label} {len(projects)} project(s)...")
    run_with_cleanup(
        _reindex_all_projects(app_config, embeddings=embeddings, batch_size=batch_size)
    )
    console.print("[green]Reindex complete[/green]")
