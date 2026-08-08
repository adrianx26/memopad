"""Doctor command for local consistency checks.

Two modes:
  - default: roundtrip test against a throwaway temp project (proves the
    file ↔ DB pipeline works in isolation).
  - --project NAME: run drift checks on a real project (--fix to repair).

Both modes also print a status report for the Tb-borrowed, feature-flagged
capabilities (G1–G7), and `--project` mode runs lightweight health probes for any
that are enabled. The capability report is informational only — it never changes
the doctor's exit code (that stays bound to file ↔ DB drift + unresolved
relations, as before).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
import typer

from memopad.cli.app import app
from memopad.cli.commands.command_utils import run_with_cleanup
from memopad.config import ConfigManager, MemoPadConfig
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


# --- Tb-borrowed capability status (G1–G7) ---
# The 7 borrowed capabilities live behind feature flags (default off, except G6
# short-term which is default ON since 0.20.2 — it is file-backed only). Doctor is
# a consistency/health tool, so it reports their state and — in --project mode —
# probes the enabled ones. This block is purely informational: it must never
# change the exit code or break the existing file ↔ DB checks.

# (flag attribute, label, hint) for the boolean capability flags.
_CAPABILITY_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("levels_enabled", "levels_enabled", "L0-L3 levels + provenance (G5)"),
    ("levels_pipeline_automatic", "levels_pipeline_automatic", "reactive distillation scheduler (G3)"),
    ("skills_enabled", "skills_enabled", "versioned skill assets (G1)"),
    ("codegraph_enabled", "codegraph_enabled", "code indexing in the graph (G2)"),
    ("shortterm_enabled", "shortterm_enabled", "in-task session context layering (G6)"),
)


def print_capability_status(config: MemoPadConfig) -> None:
    """Print the on/off state of the Tb-borrowed feature flags + G4 params.

    Pure function (no HTTP, no server): reads values straight off the config so
    it can be unit-tested without standing up a server. Called at the start of
    both doctor modes so the user always sees which new capabilities are active.
    """
    console.print("[blue]Tb-borrowed capabilities[/blue] (feature flags; default off except G6 short-term, on since 0.20.2):")

    for attr, label, hint in _CAPABILITY_FLAGS:
        # Trigger: getattr over a fixed, declared attribute list (no guessing).
        # Why: the flags are real MemoPadConfig fields; the indirection keeps the
        #      table data-driven instead of 5 copy-pasted branches.
        # Outcome: a single on/off line per capability with its one-line hint.
        enabled = bool(getattr(config, attr))
        state = "[green]on[/green]" if enabled else "[dim]off[/dim]"
        console.print(f"  {label + ':':34} {state}  [dim]({hint})[/dim]")

    # G4 retrieval budget: numeric params, 0 = disabled (current behavior).
    recall_cap = config.recall_max_chars_per_memory
    recall_timeout = config.recall_timeout_ms
    cap_state = "[green]on[/green]" if recall_cap else "[dim]off[/dim]"
    timeout_state = "[green]on[/green]" if recall_timeout else "[dim]off[/dim]"
    console.print(
        f"  {'recall_max_chars_per_memory:':34} {cap_state}  "
        f"[dim]({recall_cap}; per-memory truncation, G4)[/dim]"
    )
    console.print(
        f"  {'recall_timeout_ms:':34} {timeout_state}  "
        f"[dim]({recall_timeout}ms; retrieval timeout, G4)[/dim]"
    )


async def run_capability_probes(
    client: object, project_id: str, config: MemoPadConfig
) -> None:
    """Best-effort, informational health probes for enabled capabilities.

    Runs only in `--project` mode. Does NOT affect the exit code — these are
    status probes, not integrity failures. Failures are surfaced explicitly
    (`probe failed: <err>`) rather than swallowed, so a misconfigured gate is
    visible without aborting the whole doctor run.

    `client` is the httpx ASGI client from `get_client()`; typed as `object` to
    avoid importing httpx here just for a type hint.
    """
    knowledge_client = KnowledgeClient(client, project_id)  # type: ignore[arg-type]

    # --- G1 skills: count by status via the gated /knowledge/skills endpoint ---
    # Trigger: skills_enabled is on (server reads the same config, so the gate
    #          matches and the endpoint responds).
    # Why: gives the user a snapshot of how many skills exist and how many are
    #      still draft vs. validated.
    # Outcome: a one-line count summary; a probe failure is reported, not raised.
    if config.skills_enabled:
        try:
            statuses = ("draft", "validated", "deprecated")
            counts = {}
            for st in statuses:
                data = await knowledge_client.list_skills(status=st, limit=1, offset=0)
                counts[st] = data.get("count", 0)
            total = await knowledge_client.list_skills(limit=1, offset=0)
            console.print(
                "[blue]Skills (G1):[/blue] "
                f"draft={counts['draft']} validated={counts['validated']} "
                f"deprecated={counts['deprecated']} (total={total.get('count', 0)})"
            )
        except Exception as e:  # informational probe — surface, don't abort
            console.print(f"[yellow]Skills probe failed: {e}[/yellow]")

    # --- G6 short-term sessions: local filesystem check (file-backed, no DB) ---
    # Trigger: shortterm_enabled is on.
    # Why: session layers live on disk under <data_dir>/sessions/<id>/; doctor is
    #      a local consistency tool, so reporting session count + disk usage is
    #      the natural health signal. No endpoint exists (by design — zero DB).
    # Outcome: prints the sessions root, session count, total size, and up to 5
    #          session ids; notes "none" when the directory is absent/empty.
    if config.shortterm_enabled:
        # Mirror the G1 probe: wrap the filesystem walk so a PermissionError /
        # OSError / file-vanished-mid-iteration race is surfaced explicitly
        # (`probe failed: <err>`) instead of propagating and changing the exit
        # code. The contract is "informational only — never affects the exit code".
        try:
            sessions_root = Path(config.data_dir_path) / "sessions"
            if sessions_root.is_dir():
                session_dirs = [p for p in sessions_root.iterdir() if p.is_dir()]
                total_size = sum(
                    f.stat().st_size
                    for d in session_dirs
                    for f in d.rglob("*")
                    if f.is_file()
                )
                size_kb = total_size / 1024.0
                console.print(
                    f"[blue]Short-term sessions (G6):[/blue] {len(session_dirs)} "
                    f"session(s), {size_kb:.1f} KB under {sessions_root}"
                )
                for d in session_dirs[:5]:
                    console.print(f"    {d.name}")
                if len(session_dirs) > 5:
                    console.print(f"    ... and {len(session_dirs) - 5} more")
            else:
                console.print(
                    f"[blue]Short-term sessions (G6):[/blue] none (no {sessions_root} directory yet)"
                )
        except Exception as e:  # informational probe — surface, don't abort
            console.print(f"[yellow]Short-term sessions probe failed: {e}[/yellow]")

    # --- G3 distillation tiers: count L1/L2/L3 via the list endpoints ---
    # Trigger: levels_enabled is on (the /knowledge/facts, /scenarios, /persona
    # endpoints are always mounted, but distillation only produces entities when
    # the levels pipeline is active — gating the probe on levels_enabled keeps the
    # count meaningful and avoids noise when the feature is off).
    # Why: gives the user a snapshot of how many distilled tiers exist, mirroring
    #      the G1 skills count probe. The persona endpoint 404s when none exists,
    #      so it is wrapped in its own try/except (distinct from a probe failure).
    # Outcome: a one-line L1/L2/L3 count summary; a probe failure is reported, not
    #          raised, and never affects the exit code.
    if config.levels_enabled:
        try:
            facts = await knowledge_client.list_facts(limit=1)
            scenarios = await knowledge_client.list_scenarios(limit=1)
            # list_facts/list_scenarios return lists; use len() for the count
            # (limit=1 caps the payload — we only need presence/absence here, but
            # len() is honest about how many were returned).
            n_facts = len(facts)
            n_scenarios = len(scenarios)
            try:
                await knowledge_client.get_persona()
                n_persona = 1
            except ToolError:
                n_persona = 0
            console.print(
                "[blue]Distillation tiers (G3):[/blue] "
                f"L1 facts={n_facts} L2 scenarios={n_scenarios} L3 persona={n_persona}"
            )
        except Exception as e:  # informational probe — surface, don't abort
            console.print(f"[yellow]Distillation tiers probe failed: {e}[/yellow]")

    # --- G2 / G5 / G3: config-only hints (no safe aggregate probe) ---
    # These have no cheap "count everything" endpoint (G2 find_symbol needs a
    # name; G5/G3 state is in-memory or per-write). We surface a hint instead of
    # guessing, so the user knows what to run next.
    if config.codegraph_enabled:
        console.print(
            "[blue]CodeGraph (G2):[/blue] enabled — `memopad watch` auto-reindexes "
            "the code graph on source-file changes (full-tree, idempotent); "
            "`index_code` is the manual fallback when watch is off or a reindex "
            "fails. Query via `find_symbol` / `impact_path` / `code_context`."
        )
    if config.levels_enabled:
        console.print(
            "[blue]Levels (G5):[/blue] enabled — provenance enforced at write time; "
            "`drill_down` traces L3 → L1 → L0 sources."
        )
    if config.levels_pipeline_automatic:
        console.print(
            "[blue]Distillation scheduler (G3):[/blue] active — emits triggers on "
            "every write; the code-only distiller (L1 facts → L2 scenarios → L3 "
            "persona) runs automatically. `memopad distill` / `distill_memory` are "
            "the on-demand surface."
        )


async def run_doctor() -> None:
    """Run local consistency checks for file <-> database flows."""
    console.print("[blue]Running Memopad doctor checks...[/blue]")
    # Capability status first (informational; never affects the roundtrip below).
    print_capability_status(ConfigManager().load_config())
    console.print()

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

        # Capability status + (later) probes — informational, never affects the
        # drift `remaining` count below.
        app_config = ConfigManager().load_config()
        print_capability_status(app_config)

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

        # --- Step 4: capability probes for enabled Tb-borrowed features ---
        # Informational only — does not contribute to `remaining`. See
        # run_capability_probes for why failures are surfaced, not raised.
        await run_capability_probes(client, project_id, app_config)

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
) -> None:
    """Run local consistency checks to verify file/database sync."""
    try:
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
