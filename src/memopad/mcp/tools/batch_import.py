"""Batch import tool for Memopad MCP server."""

from typing import Optional
from loguru import logger
from fastmcp import Context

from memopad.mcp.server import mcp
from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project, add_project_metadata

@mcp.tool(
    description="Batch import a directory of markdown files into the Memopad project.",
)
async def batch_import_directory(
    source_directory: str,
    destination_folder: str = "imported",
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Import a local directory of markdown files into the current Memopad project.
    
    Reads all `.md` files recursively in the source directory, normalizes their frontmatter,
    and writes them into the active project under the specified destination folder.
    
    Args:
        source_directory: Absolute path to the directory containing the markdown files to import.
        destination_folder: The relative folder path in the project to place imported files (default: "imported").
        project: Project name to import to. Optional - server will resolve using hierarchy.
        context: Optional FastMCP context.
        
    Returns:
        A markdown formatted summary of the import results.
    """
    logger.info(f"Batch importing from {source_directory} to project {project}")

    async with get_client() as client:
        # Resolve active project
        active_project = await get_active_project(client, project, context)

        # Import here to avoid circular dependencies
        from memopad.markdown.entity_parser import EntityParser
        from memopad.markdown.markdown_processor import MarkdownProcessor
        from memopad.services.file_service import FileService
        from memopad.importers.markdown_importer import MarkdownImporter
        from memopad.config import ConfigManager

        # Build dependencies
        entity_parser = EntityParser(active_project.home)
        
        # Load configurations
        config_manager = ConfigManager()
        app_config = config_manager.load_config()
        
        markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
        file_service = FileService(active_project.home, markdown_processor, app_config=app_config)
        
        # Instantiate Importer
        importer = MarkdownImporter(
            base_path=active_project.home,
            markdown_processor=markdown_processor,
            file_service=file_service,
        )

        # Run import
        result = await importer.import_data(
            source_data=source_directory,
            destination_folder=destination_folder,
        )

        if result.success:
            return f"# Batch Import Complete\n\n- **Entities Imported**: {result.entities}\n- **Relations Extracted**: {result.relations}\n- **Files Skipped**: {result.skipped_entities}"
        else:
            return f"# Batch Import Failed\n\n{result.error_message}"
