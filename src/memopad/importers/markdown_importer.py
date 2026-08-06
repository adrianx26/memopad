"""Markdown directory import service for Memopad."""

import logging
from pathlib import Path
from typing import Any, Optional

from memopad.markdown.entity_parser import EntityParser
from memopad.importers.base import Importer
from memopad.schemas.importer import EntityImportResult

logger = logging.getLogger(__name__)


class MarkdownImporter(Importer[EntityImportResult]):
    """Service for batch importing Markdown files from a directory."""

    def handle_error(
        self, message: str, error: Optional[Exception] = None
    ) -> EntityImportResult:
        """Return a failed EntityImportResult with an error message."""
        error_msg = f"{message}: {error}" if error else message
        return EntityImportResult(
            import_count={},
            success=False,
            error_message=error_msg,
            entities=0,
            relations=0,
            skipped_entities=0,
        )

    async def import_data(
        self, source_data: str, destination_folder: str = "imported", **kwargs: Any
    ) -> EntityImportResult:
        """Import markdown files from a source directory to the destination folder.

        Args:
            source_data: Path to the source directory containing markdown files.
            destination_folder: Destination folder within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            EntityImportResult containing statistics and status of the import.
        """
        try:
            source_path = Path(source_data)
            if not source_path.exists() or not source_path.is_dir():
                return self.handle_error(f"Source directory does not exist: {source_path}")

            # Ensure the destination folder exists
            if destination_folder:
                await self.ensure_folder_exists(destination_folder)

            entities_created = 0
            relations_count = 0
            skipped_entities = 0

            # Find all markdown files recursively
            md_files = list(source_path.rglob("*.md"))
            
            # Temporary parser just to read/normalize the frontmatter and content structure
            parser = EntityParser(source_path)

            for md_file in md_files:
                try:
                    rel_path = md_file.relative_to(source_path)
                    content = md_file.read_text(encoding="utf-8")

                    # Parse to ensure it conforms to Memopad's expected format
                    entity = await parser.parse_markdown_content(
                        file_path=md_file,
                        content=content,
                        mtime=md_file.stat().st_mtime,
                        ctime=md_file.stat().st_ctime,
                    )
                    
                    # Ensure the subdirectories exist in the destination
                    target_dir = f"{destination_folder}/{rel_path.parent.as_posix()}" if destination_folder else rel_path.parent.as_posix()
                    if target_dir.endswith("/."):
                        target_dir = target_dir[:-2]
                    
                    if target_dir and target_dir != "." and target_dir != "":
                        await self.file_service.ensure_directory(target_dir)

                    # Determine file path
                    file_path = f"{destination_folder}/{rel_path.as_posix()}" if destination_folder else rel_path.as_posix()
                    
                    # Rewrite the entity so it gets consistent frontmatter and formatting
                    await self.write_entity(entity, file_path)
                    
                    entities_created += 1
                    relations_count += len(entity.relations)

                except Exception as e:
                    logger.warning(f"Failed to import file {md_file}: {e}")
                    skipped_entities += 1

            return EntityImportResult(
                import_count={"entities": entities_created, "relations": relations_count, "skipped": skipped_entities},
                success=True,
                entities=entities_created,
                relations=relations_count,
                skipped_entities=skipped_entities,
            )

        except Exception as e:
            logger.exception(f"Failed to import markdown directory: {source_data}")
            return self.handle_error("Failed to import markdown directory", e)
