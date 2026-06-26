"""Schemas for import services."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ImportResult(BaseModel):
    """Common import result schema."""

    import_count: Dict[str, int]
    success: bool
    error_message: Optional[str] = None
    # Per-item errors collected during import (e.g. individual files that failed
    # to parse/write). Previously these were only logged and lost; surfacing them
    # lets callers report what actually went wrong instead of just a skip count.
    errors: List[str] = Field(default_factory=list)


class ChatImportResult(ImportResult):
    """Result schema for chat imports."""

    conversations: int = 0
    messages: int = 0


class ProjectImportResult(ImportResult):
    """Result schema for project imports."""

    documents: int = 0
    prompts: int = 0


class EntityImportResult(ImportResult):
    """Result schema for entity imports."""

    entities: int = 0
    relations: int = 0
    skipped_entities: int = 0
