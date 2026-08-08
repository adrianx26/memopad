"""Standalone factory for ``DistillationService`` (mirrors ``get_sync_service``).

Used by the ``DistillationDispatcher`` (the scheduler callback) and the CLI/doctor
paths — anywhere a per-project service must be built outside the FastAPI
dependency-injection graph. The DI-provided variant lives in ``deps/services.py``
(Task #6); this is the async standalone constructor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from memopad import db
from memopad.config import ConfigManager
from memopad.markdown import EntityParser, MarkdownProcessor
from memopad.models import Project
from memopad.repository.entity_repository import EntityRepository
from memopad.repository.observation_repository import ObservationRepository
from memopad.repository.relation_repository import RelationRepository
from memopad.repository.search_repository import create_search_repository
from memopad.services import EntityService, FileService
from memopad.services.distillation_scheduler import DistillationScheduler
from memopad.services.distillation_service import DistillationService, FactExtractor
from memopad.services.embedding_service import EmbeddingService
from memopad.services.link_resolver import LinkResolver
from memopad.services.search_service import SearchService


async def get_distillation_service(
    project: Project,
    *,
    scheduler: Optional[DistillationScheduler] = None,
    extractor: Optional[FactExtractor] = None,
) -> DistillationService:  # pragma: no cover - integration factory
    """Build a per-project ``DistillationService`` with all wired dependencies.

    Mirrors ``sync/sync_service.py:get_sync_service`` for repo/service wiring and
    adds the distillation-specific dependencies: an opt-in ``EmbeddingService``
    (``None`` when ``MEMOPAD_EMBEDDINGS_ENABLED`` is off — the service then falls
    back to token-Jaccard similarity) and the on-disk state watermarks.
    """
    app_config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
    )

    project_path = Path(project.path)
    entity_parser = EntityParser(project_path)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(project_path, markdown_processor, app_config=app_config)

    entity_repository = EntityRepository(session_maker, project_id=project.id)
    observation_repository = ObservationRepository(session_maker, project_id=project.id)
    relation_repository = RelationRepository(session_maker, project_id=project.id)
    search_repository = create_search_repository(session_maker, project_id=project.id)

    search_service = SearchService(search_repository, entity_repository, file_service)
    link_resolver = LinkResolver(entity_repository, search_service)
    entity_service = EntityService(
        entity_parser,
        entity_repository,
        observation_repository,
        relation_repository,
        file_service,
        link_resolver,
        app_config=app_config,
    )

    embedding_service = EmbeddingService.maybe_create(session_maker, project.id)
    state_path = (
        Path(app_config.data_dir_path) / "distillation" / f"project-{project.id}-state.json"
    )

    return DistillationService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        entity_service=entity_service,
        app_config=app_config,
        project_id=project.id,
        extractor=extractor,
        scheduler=scheduler,
        embedding_service=embedding_service,
        state_path=state_path,
    )