"""Doctor command for local consistency checks.

Two modes:
  - default: roundtrip test against a throwaway temp project (proves the
    file ↔ DB pipeline works in isolation).
  - --project NAME: run drift checks on a real project (--fix to repair).
"""

from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path

import aiosqlite
from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
import typer

from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.config import APP_DATABASE_NAME, DATA_DIR_NAME
from memopad.markdown.entity_parser import EntityParser
from memopad.markdown.markdown_processor import MarkdownProcessor
from memopad.markdown.schemas import EntityFrontmatter, EntityMarkdown
from memopad.mcp.async_client import get_client
from memopad.mcp.clients import KnowledgeClient, ProjectClient, SearchClient
from memopad.mcp.tools.utils import call_get, call_post
from memopad.schemas.base import Entity
from memopad.schemas.project_info import ProjectInfoRequest
from memopad.schemas.search import SearchQuery
from memopad.schemas import SyncReportResponse

console = Console()

# Dim-scoped vec0 table names look like ``embedding_vec_<item_type>_p<project>_d<dim>``
# (see EmbeddingService._vec_table). Any vec0 *main* virtual table that does
# NOT match this pattern is a leftover from before dim-scoping and would
# cause wrong-dim inserts to roll back canonical BLOB writes on a model swap.
#
# sqlite-vec creates shadow tables for each virtual table (``_info``,
# ``_chunks``, ``_rowids``, ``_vector_chunksNN``); those carry an extra suffix
# after the project/dim segment, so they are excluded by anchoring the
# main-table patterns at the end of the name.
_VEC_TABLE_DIM_SCOPED = re.compile(r"^embedding_vec_[a-z]+_p\d+_d\d+$")
# A main vec0 table is either dim-scoped (above) or legacy ``..._p<project>``
# with nothing after the project id. Shadow tables have a trailing suffix and
# match neither pattern, so they're filtered out before the legacy check.
_VEC_TABLE_MAIN = re.compile(r"^embedding_vec_[a-z]+_p\d+(_d\d+)?$")


async def run_health_checks() -> int:
    """Inspect the local app DB schema for invariants introduced recently.

    These are read-only checks against the app-level SQLite database
    (``~/memopad/memory.db``). They do not talk to the MCP server and do not
    mutate anything. Returns the number of issues found.

    Checks:
      1a. ``reindex_state`` table exists with a ``fingerprint`` column — the
          per-entity SHA-256 fingerprint that makes incremental reindex skip
          unchanged entities. Missing ⇒ incremental reindex is disabled.
      1b. ``embedding`` table has a ``content_hash`` column — the per-item
          SHA-256 that lets the embedding service skip re-embedding unchanged
          text. Missing ⇒ embedding dedup is disabled. (The ``content_hash``
          column lives on the ``embedding`` table, added by migration
          ``p9d1e2f3a4b5``; it is NOT on ``reindex_state``, whose own
          per-entity fingerprint column is ``fingerprint``.)
      2. Every ``embedding_vec_*`` virtual table is dim-scoped
         (``..._p<project>_d<dim>``). Non-scoped leftovers break model swaps.

    Cache invalidation (permalink/metadata caches) is a behavioural
    invariant exercised by the roundtrip below, not a schema one, so it is
    not checked here.
    """
    console.print("[blue]Running schema health checks...[/blue]")
    db_path = Path.home() / DATA_DIR_NAME / APP_DATABASE_NAME
    if not db_path.exists():
        console.print(
            "[yellow]App DB not found — skipping schema health checks "
            "(run `memopad` once to initialise it).[/yellow]"
        )
        return 0

    issues = 0
    async with aiosqlite.connect(str(db_path)) as conn:
        # --- Check 1a: reindex_state + fingerprint (incremental reindex) ---
        # reindex_state.fingerprint is the per-entity SHA-256 that lets
        # reindex_all skip unchanged entities (migration o9c0d1e2f3a4).
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reindex_state'"
        )
        if await cur.fetchone() is None:
            console.print(
                "[yellow]reindex_state table missing — incremental reindex is "
                "disabled (run migrations).[/yellow]"
            )
            issues += 1
        else:
            cur = await conn.execute("PRAGMA table_info(reindex_state)")
            columns = {row[1] for row in await cur.fetchall()}
            if "fingerprint" not in columns:
                console.print(
                    "[yellow]reindex_state.fingerprint missing — per-entity "
                    "fingerprint absent, reindex can't skip unchanged rows.[/yellow]"
                )
                issues += 1
            else:
                console.print(
                    "[green]OK[/green] reindex_state.fingerprint present "
                    "(incremental reindex enabled)"
                )

        # --- Check 1b: embedding.content_hash (embedding dedup) ---
        # content_hash lives on the embedding table (migration p9d1e2f3a4b5),
        # NOT on reindex_state. It lets upsert_batch skip re-embedding
        # unchanged text+model items.
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='embedding'"
        )
        if await cur.fetchone() is None:
            # Embedding table is created lazily on first embedding index, so its
            # absence is not an error — just means embeddings never ran.
            console.print(
                "[green]OK[/green] embedding table not yet created "
                "(created on first embedding index)"
            )
        else:
            cur = await conn.execute("PRAGMA table_info(embedding)")
            columns = {row[1] for row in await cur.fetchall()}
            if "content_hash" not in columns:
                console.print(
                    "[yellow]embedding.content_hash missing — embedding dedup "
                    "disabled (run migrations).[/yellow]"
                )
                issues += 1
            else:
                console.print(
                    "[green]OK[/green] embedding.content_hash present "
                    "(embedding dedup enabled)"
                )

        # --- Check 2: vec0 table dim-scoping ---
        # Only inspect main virtual tables; sqlite-vec's shadow tables
        # (``_info``/``_chunks``/``_rowids``/``_vector_chunksNN``) are filtered
        # out by ``_VEC_TABLE_MAIN``.
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'embedding\\_vec\\_%' ESCAPE '\\'"
        )
        all_vec_names = [row[0] for row in await cur.fetchall()]
        vec_tables = [n for n in all_vec_names if _VEC_TABLE_MAIN.match(n)]
        legacy = [n for n in vec_tables if not _VEC_TABLE_DIM_SCOPED.match(n)]
        if legacy:
            console.print(
                f"[yellow]{len(legacy)} legacy (non-dim-scoped) vec0 table(s) "
                f"found — model swaps can break until they are dropped: "
                f"{legacy}[/yellow]"
            )
            issues += len(legacy)
        elif vec_tables:
            console.print(
                f"[green]OK[/green] all {len(vec_tables)} vec0 table(s) "
                "are dim-scoped"
            )
        else:
            console.print(
                "[green]OK[/green] no vec0 tables yet "
                "(created on first embedding index)"
            )

    if issues:
        console.print(f"[yellow]Schema health: {issues} issue(s).[/yellow]")
    else:
        console.print("[green]Schema health checks passed.[/green]")
    return issues


async def run_doctor() -> None:
    """Run local consistency checks for file <-> database flows."""
    console.print("[blue]Running Memopad doctor checks...[/blue]")

    # Schema health checks run first and are best-effort: a schema issue should
    # be reported but must not abort the functional roundtrip below.
    try:
        await run_health_checks()
    except Exception as e:  # pragma: no cover
        console.print(f"[yellow]Schema health checks skipped: {e}[/yellow]")

    project_name = f"doctor-{uuid.uuid4().hex[:8]}"
    api_note_title = "Doctor API Note"
    manual_note_title = "Doctor Manual Note"
    manual_permalink = "doctor/manual-note"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        async with get_client() as client:
            project_client = ProjectClient(client)
            project_request = ProjectInfoRequest(
                name=project_name,
                path=str(temp_path),
                set_default=False,
            )

            project_id: str | None = None

            try:
                status = await project_client.create_project(project_request.model_dump())
                if not status.new_project:
                    raise ValueError("Failed to create doctor project")
                project_id = status.new_project.external_id
                console.print(f"[green]OK[/green] Created doctor project: {project_name}")

                # --- DB -> File: create an entity via API ---
                knowledge_client = KnowledgeClient(client, project_id)
                api_note = Entity(
                    title=api_note_title,
                    directory="doctor",
                    entity_type="note",
                    content_type="text/markdown",
                    content=f"# {api_note_title}\n\n- [note] API to file check",
                    entity_metadata={"tags": ["doctor"]},
                )
                api_result = await knowledge_client.create_entity(api_note.model_dump(), fast=False)

                api_file = temp_path / api_result.file_path
                if not api_file.exists():
                    raise ValueError(f"API note file missing: {api_result.file_path}")

                api_text = api_file.read_text(encoding="utf-8")
                if api_note_title not in api_text:
                    raise ValueError("API note content missing from file")

                console.print("[green]OK[/green] API write created file")

                # --- File -> DB: write markdown file directly, then sync ---
                parser = EntityParser(temp_path)
                processor = MarkdownProcessor(parser)
                manual_markdown = EntityMarkdown(
                    frontmatter=EntityFrontmatter(
                        metadata={
                            "title": manual_note_title,
                            "type": "note",
                            "permalink": manual_permalink,
                            "tags": ["doctor"],
                        }
                    ),
                    content=f"# {manual_note_title}\n\n- [note] File to DB check",
                )

                manual_path = temp_path / "doctor" / "manual-note.md"
                await processor.write_file(manual_path, manual_markdown)
                console.print("[green]OK[/green] Manual file written")

                sync_response = await call_post(
                    client,
                    f"/v2/projects/{project_id}/sync?force_full=true&run_in_background=false",
                )
                sync_report = SyncReportResponse.model_validate(sync_response.json())
                if sync_report.total == 0:
                    raise ValueError("Sync did not detect any changes")

                console.print("[green]OK[/green] Sync indexed manual file")

                search_client = SearchClient(client, project_id)
                search_query = SearchQuery(title=manual_note_title)
                search_results = await search_client.search(
                    search_query.model_dump(), page=1, page_size=5
                )
                if not any(result.title == manual_note_title for result in search_results.results):
                    raise ValueError("Manual note not found in search index")

                console.print("[green]OK[/green] Search confirmed manual file")

                status_response = await call_post(client, f"/v2/projects/{project_id}/status")
                status_report = SyncReportResponse.model_validate(status_response.json())
                if status_report.total != 0:
                    raise ValueError("Project status not clean after sync")

                console.print("[green]OK[/green] Status clean after sync")

            finally:
                if project_id:
                    await project_client.delete_project(project_id)

    console.print("[green]Doctor checks passed.[/green]")


async def run_drift_check(project_name: str, fix: bool) -> int:
    """Inspect a real project for file ↔ DB drift, optionally repairing.

    What it checks:
      1. Files on disk that the DB doesn't know about → counted as "new" by sync.
      2. DB entities whose files are gone → counted as "deleted" by sync.
      3. Unresolved relations (broken [[wikilinks]]).

    With --fix: triggers a force_full sync, which is the canonical way to
    reconcile (1) and (2). Unresolved relations are *reported* but not
    auto-rewritten — fuzzy fixing user content is risky and best left to a
    human review step.

    Returns the count of remaining issues after the run.
    """
    async with get_client() as client:
        project_client = ProjectClient(client)
        projects = await project_client.list_projects()
        target = next((p for p in projects.projects if p.name == project_name), None)
        if not target:
            console.print(f"[red]Project '{project_name}' not found.[/red]")
            return 1

        project_id = target.external_id
        console.print(f"[blue]Inspecting project '{project_name}'...[/blue]")

        # --- Step 1: drift report (always runs, fix or no fix) ---
        status_response = await call_post(client, f"/v2/projects/{project_id}/status")
        status_report = SyncReportResponse.model_validate(status_response.json())

        # SyncReportResponse fields are guaranteed by the schema — use direct
        # attribute access instead of getattr.
        new_files = status_report.new
        modified = status_report.modified
        deleted = status_report.deleted
        moves = status_report.moves

        console.print(f"  new files (on disk, not in DB): {len(new_files)}")
        console.print(f"  modified (disk newer than DB):  {len(modified)}")
        console.print(f"  deleted (in DB, file gone):     {len(deleted)}")
        console.print(f"  moved:                          {len(moves)}")

        # --- Step 2: optional repair ---
        if fix and (new_files or modified or deleted or moves):
            console.print("[yellow]--fix: running force_full sync to reconcile...[/yellow]")
            sync_response = await call_post(
                client,
                f"/v2/projects/{project_id}/sync?force_full=true&run_in_background=false",
            )
            sync_report = SyncReportResponse.model_validate(sync_response.json())
            console.print(
                f"[green]OK[/green] sync reconciled: total={sync_report.total}"
            )

        # --- Step 3: surface unresolved relations as warnings ---
        # Trigger: sync resolves [[wikilinks]] when target entities exist.
        # Why: any remaining unresolved relation is a broken link the user
        #      needs to either fix in the source file or by creating the target.
        # Outcome: report-only — we do NOT rewrite user files automatically.
        try:
            unresolved_resp = await call_get(
                client, f"/v2/projects/{project_id}/sync/unresolved"
            )
            unresolved = unresolved_resp.json().get("relations", [])
        except Exception:
            unresolved = []

        if unresolved:
            console.print(
                f"[yellow]{len(unresolved)} unresolved relations[/yellow] "
                "(broken [[wikilinks]] — fix manually or create the target notes)"
            )
            for r in unresolved[:10]:
                from_p = r.get("from_permalink") or "(unknown)"
                console.print(f"    {from_p} -[{r.get('relation_type')}]-> {r.get('to_name')}")
            if len(unresolved) > 10:
                console.print(f"    ... and {len(unresolved) - 10} more")
        else:
            console.print("[green]OK[/green] no unresolved relations")

        remaining = (
            len(new_files) + len(modified) + len(deleted) + len(moves)
            if not fix
            else 0
        ) + len(unresolved)
        return remaining


@app.command()
def doctor(
    project: str | None = typer.Option(
        None,
        "--project",
        help="Run drift checks against the named project instead of the temp roundtrip.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="With --project: run a force_full sync to reconcile file ↔ DB drift.",
    ),
    health: bool = typer.Option(
        False,
        "--health",
        help="Only run local schema health checks (reindex_state fingerprint, "
        "embedding content_hash, vec0 dim-scoping) against the app DB; skip "
        "the roundtrip.",
    ),
) -> None:
    """Run local consistency checks to verify file/database sync."""
    try:
        if health:
            issues = run_with_cleanup(run_health_checks())
            if issues:
                console.print(
                    f"[yellow]{issues} schema issue(s) found.[/yellow]"
                )
                raise typer.Exit(code=1)
            return
        if project:
            remaining = run_with_cleanup(run_drift_check(project, fix))
            if remaining:
                console.print(
                    f"[yellow]{remaining} issues remaining "
                    f"(re-run with --fix or address manually).[/yellow]"
                )
                raise typer.Exit(code=1)
            console.print("[green]Project is clean.[/green]")
        else:
            run_with_cleanup(run_doctor())
    except (ToolError, ValueError) as e:
        console.print(f"[red]Doctor failed: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Doctor failed: {e}")
        typer.echo(f"Doctor failed: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover
