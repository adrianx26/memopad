"""Storage optimization tool for Memopad MCP server.

Provides storage optimization and cleanup operations for Memopad knowledge bases.
"""

from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project
from memopad.mcp.server import mcp


@mcp.tool(
    description="Optimize Memopad storage by removing duplicates and optimizing file sizes.",
)
async def optimize_storage(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Optimize Memopad storage by checking for duplicates and optimizing file sizes.

    This tool analyzes the knowledge base and provides storage optimization suggestions.
    Currently, it checks for duplicate files and provides statistics about storage usage.

    Args:
        project: Project name to optimize storage for. Optional - server will use default.
        context: Optional FastMCP context for performance caching.

    Returns:
        Formatted optimization report with statistics and results

    Examples:
        # Optimize storage in default project
        optimize_storage()

        # Optimize storage in specific project
        optimize_storage(project="work-docs")

    Raises:
        ToolError: If project doesn't exist or optimization fails
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)

        logger.debug(f"Optimizing storage for project: {active_project.name}")

        # Import here to avoid circular import
        from memopad.services.optimization_service import StorageOptimizer

        optimizer = StorageOptimizer(active_project)
        
        # Get storage statistics
        usage = await optimizer.get_storage_usage()
        
        # Run optimization
        result = await optimizer.optimize()

        # Build report
        lines = []
        lines.append(f"# Storage Optimization Report for {active_project.name}")
        lines.append("")
        lines.append("## Current Storage Usage")
        lines.append(f"- Total files: {usage.total_files}")
        lines.append(f"- Total size: {usage.total_size / (1024 * 1024):.2f} MB")
        lines.append(f"- Average file size: {usage.avg_file_size:.2f} KB")
        lines.append(f"- Largest file: {usage.largest_file_size / 1024:.2f} KB")
        lines.append("")
        
        lines.append("## Optimization Results")
        lines.append(f"- Files processed: {result.processed_count}")
        lines.append(f"- Files optimized: {result.optimized_count}")
        lines.append(f"- Storage saved: {result.storage_saved / (1024 * 1024):.2f} MB")
        lines.append(f"- Storage reduction: {result.reduction_percentage:.1f}%")
        lines.append("")
        
        if result.optimized_files:
            lines.append("## Optimized Files")
            for file_info in result.optimized_files:
                original_size = file_info['original_size'] / 1024
                new_size = file_info['new_size'] / 1024
                saved = file_info['saved'] / 1024
                percentage = file_info['reduction_percentage']
                
                lines.append(f"- **{file_info['filename']}**")
                lines.append(f"  Original: {original_size:.1f} KB")
                lines.append(f"  Optimized: {new_size:.1f} KB")
                lines.append(f"  Saved: {saved:.1f} KB ({percentage:.1f}%)")
                lines.append("")
        
        if result.skipped_files:
            lines.append("## Skipped Files")
            for filename in result.skipped_files:
                lines.append(f"- {filename}")
            lines.append("")
        
        if result.errors:
            lines.append("## Errors")
            for error in result.errors:
                lines.append(f"- {error}")
            lines.append("")
        
        return "\n".join(lines)
