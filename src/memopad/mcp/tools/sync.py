"""Sync tool for Memopad MCP server."""

from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.server import mcp
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project


@mcp.tool(
    description="Synchronize a project's database index with the actual files on disk.",
)
async def sync_project_files(
    force_full: bool = False,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Synchronize a project's filesystem to the database index.
    
    This tool triggers a sync process that scans the project's directory for any 
    new, modified, or deleted files that the database might have missed (e.g., 
    changes made externally) and updates the index accordingly.
    
    Args:
        force_full: If true, ignore timestamps and do a full rescan of all files.
        project: Project name. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
        
    Returns:
        A markdown formatted string detailing the results of the sync operation.
    """
    logger.info(f"Triggering sync for project {project} (force_full={force_full})")

    async with get_client() as client:
        # Resolve active project
        active_project = await get_active_project(client, project, context)

        # Import here to avoid circular dependencies
        from memopad.mcp.clients.project import ProjectClient
        
        project_client = ProjectClient(client)
        
        try:
            report = await project_client.sync_project(
                project_external_id=active_project.external_id,
                force_full=force_full
            )
        except Exception as e:
            logger.exception("Sync failed")
            return f"# Sync Failed\n\nFailed to synchronize project '{active_project.name}': {e}"
            
        return (
            f"# Sync Complete: {active_project.name}\n\n"
            f"- **New Files Processed**: {len(report.new)}\n"
            f"- **Files Modified**: {len(report.modified)}\n"
            f"- **Files Deleted**: {len(report.deleted)}\n"
            f"- **Files Moved**: {len(report.moves)}\n"
            f"- **Total Changes Detected**: {report.total}\n"
        )
