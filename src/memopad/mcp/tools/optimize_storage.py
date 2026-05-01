"""Storage optimization tool for Memopad MCP server.

Detects duplicate notes and (with dry_run=False) replaces each duplicate's body
with a redirect wikilink to the canonical copy. Default is dry-run so the user
can review the report before any files are touched.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description=(
        "Detect duplicate notes in a project and (with dry_run=False) merge them "
        "by replacing each duplicate's body with a wikilink redirect to the canonical copy."
    ),
)
async def optimize_storage(
    project: Optional[str] = None,
    dry_run: bool = True,
    context: Context | None = None,
) -> str:
    """Find and optionally merge duplicate notes.

    Two notes are considered duplicates when their content (after frontmatter is
    stripped and whitespace canonicalized) hashes to the same value. README.md,
    index.md, and .gitignore are excluded — those are commonly duplicated across
    directories on purpose.

    Args:
        project: Project name. Optional — server resolves the default.
        dry_run: When True (default) only reports what would change. Set to False
                 to actually rewrite duplicate files.
        context: Optional FastMCP context.

    Returns:
        Markdown report listing duplicate groups, sizes, and (when applied) the
        number of files rewritten and bytes reclaimed.

    Examples:
        # See what duplicates exist (safe)
        optimize_storage()

        # Actually merge duplicates
        optimize_storage(dry_run=False)
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"optimize_storage: project={active_project.name} dry_run={dry_run}"
        )

        # Imported here to avoid pulling the heavy walk logic into modules that
        # only register tools at startup.
        from memopad.services.optimization_service import StorageOptimizer, format_report

        optimizer = StorageOptimizer(active_project)
        usage = await optimizer.get_storage_usage()
        result = await optimizer.optimize(dry_run=dry_run)

        report = format_report(usage, result, active_project.name)
        return add_project_metadata(report, active_project.name)
