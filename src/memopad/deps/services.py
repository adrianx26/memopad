"""Service dependency injection for memopad.

This module provides service-layer dependencies:
- EntityParser, MarkdownProcessor
- FileService, EntityService
- SearchService, LinkResolver, ContextService
- SyncService, ProjectService, DirectoryService
"""

import asyncio
from pathlib import Path
from typing import Annotated, Any, Callable, Coroutine, Mapping, Protocol

from fastapi import Depends
from loguru import logger

from memopad.deps.config import AppConfigDep
from memopad.deps.db import SessionMakerDep
from memopad.deps.projects import (
    ProjectConfigDep,
    ProjectConfigV2Dep,
    ProjectConfigV2ExternalDep,
    ProjectExternalIdPathDep,
    ProjectRepositoryDep,
)
from memopad.deps.repositories import (
    EntityRepositoryDep,
    EntityRepositoryV2Dep,
    EntityRepositoryV2ExternalDep,
    EntityAliasRepositoryDep,
    EntityAliasRepositoryV2Dep,
    EntityAliasRepositoryV2ExternalDep,
    ObservationRepositoryDep,
    ObservationRepositoryV2Dep,
    ObservationRepositoryV2ExternalDep,
    RelationRepositoryDep,
    RelationRepositoryV2Dep,
    RelationRepositoryV2ExternalDep,
    SearchRepositoryDep,
    SearchRepositoryV2Dep,
    SearchRepositoryV2ExternalDep,
    ObservationSchemaRepositoryDep,
    ObservationSchemaRepositoryV2Dep,
    ObservationSchemaRepositoryV2ExternalDep,
)
from memopad.markdown import EntityParser
from memopad.markdown.markdown_processor import MarkdownProcessor
from memopad.services import EntityService, ProjectService
from memopad.services.context_service import ContextService
from memopad.services.directory_service import DirectoryService
from memopad.services.file_service import FileService
from memopad.services.link_resolver import LinkResolver
from memopad.services.search_service import SearchService
from memopad.services.conflict_service import ConflictService
from memopad.services.codegraph_service import CodeGraphService
from memopad.services.distillation_scheduler import (
    DistillationScheduler,
    PipelineConfig,
)
from memopad.services.distillation_service import (
    DistillationDispatcher,
    DistillationService,
)
from memopad.services.embedding_service import EmbeddingService
from memopad.services.schema_service import SchemaService
from memopad.sync import SyncService

# --- Entity Parser ---


async def get_entity_parser(project_config: ProjectConfigDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserDep = Annotated["EntityParser", Depends(get_entity_parser)]


async def get_entity_parser_v2(
    project_config: ProjectConfigV2Dep,
) -> EntityParser:  # pragma: no cover
    return EntityParser(project_config.home)


EntityParserV2Dep = Annotated["EntityParser", Depends(get_entity_parser_v2)]


async def get_entity_parser_v2_external(project_config: ProjectConfigV2ExternalDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserV2ExternalDep = Annotated["EntityParser", Depends(get_entity_parser_v2_external)]


# --- Markdown Processor ---


async def get_markdown_processor(
    entity_parser: EntityParserDep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorDep = Annotated[MarkdownProcessor, Depends(get_markdown_processor)]


async def get_markdown_processor_v2(  # pragma: no cover
    entity_parser: EntityParserV2Dep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorV2Dep = Annotated[MarkdownProcessor, Depends(get_markdown_processor_v2)]


async def get_markdown_processor_v2_external(
    entity_parser: EntityParserV2ExternalDep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorV2ExternalDep = Annotated[
    MarkdownProcessor, Depends(get_markdown_processor_v2_external)
]


# --- File Service ---


async def get_file_service(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home} "
    )
    return file_service


FileServiceDep = Annotated[FileService, Depends(get_file_service)]


async def get_file_service_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home}"
    )
    return file_service


FileServiceV2Dep = Annotated[FileService, Depends(get_file_service_v2)]


async def get_file_service_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home}"
    )
    return file_service


FileServiceV2ExternalDep = Annotated[FileService, Depends(get_file_service_v2_external)]


# --- Search Service ---


async def get_search_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    file_service: FileServiceDep,
) -> SearchService:
    """Create SearchService with dependencies."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


async def get_search_service_v2(  # pragma: no cover
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    file_service: FileServiceV2Dep,
) -> SearchService:
    """Create SearchService for v2 API."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceV2Dep = Annotated[SearchService, Depends(get_search_service_v2)]


async def get_search_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> SearchService:
    """Create SearchService for v2 API (uses external_id)."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceV2ExternalDep = Annotated[SearchService, Depends(get_search_service_v2_external)]


# --- Link Resolver ---


async def get_link_resolver(
    entity_repository: EntityRepositoryDep,
    search_service: SearchServiceDep,
    alias_repository: EntityAliasRepositoryDep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        alias_repository=alias_repository,
    )


LinkResolverDep = Annotated[LinkResolver, Depends(get_link_resolver)]


async def get_link_resolver_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    search_service: SearchServiceV2Dep,
    alias_repository: EntityAliasRepositoryV2Dep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        alias_repository=alias_repository,
    )


LinkResolverV2Dep = Annotated[LinkResolver, Depends(get_link_resolver_v2)]


async def get_link_resolver_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    alias_repository: EntityAliasRepositoryV2ExternalDep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        alias_repository=alias_repository,
    )


LinkResolverV2ExternalDep = Annotated[LinkResolver, Depends(get_link_resolver_v2_external)]


# --- Conflict Service ---


async def get_conflict_service(
    observation_repository: ObservationRepositoryDep,
) -> ConflictService:
    return ConflictService(observation_repository)


ConflictServiceDep = Annotated[ConflictService, Depends(get_conflict_service)]


async def get_schema_service(
    observation_schema_repository: ObservationSchemaRepositoryDep,
) -> SchemaService:
    return SchemaService(observation_schema_repository)


SchemaServiceDep = Annotated[SchemaService, Depends(get_schema_service)]


async def get_schema_service_v2(  # pragma: no cover
    observation_schema_repository: ObservationSchemaRepositoryV2Dep,
) -> SchemaService:
    return SchemaService(observation_schema_repository)


SchemaServiceV2Dep = Annotated[SchemaService, Depends(get_schema_service_v2)]


async def get_schema_service_v2_external(
    observation_schema_repository: ObservationSchemaRepositoryV2ExternalDep,
) -> SchemaService:
    return SchemaService(observation_schema_repository)


SchemaServiceV2ExternalDep = Annotated[SchemaService, Depends(get_schema_service_v2_external)]


async def get_conflict_service_v2(  # pragma: no cover
    observation_repository: ObservationRepositoryV2Dep,
) -> ConflictService:
    return ConflictService(observation_repository)


ConflictServiceV2Dep = Annotated[ConflictService, Depends(get_conflict_service_v2)]


async def get_conflict_service_v2_external(
    observation_repository: ObservationRepositoryV2ExternalDep,
) -> ConflictService:
    return ConflictService(observation_repository)


ConflictServiceV2ExternalDep = Annotated[
    ConflictService, Depends(get_conflict_service_v2_external)
]


# --- Entity Service ---


async def get_entity_service(
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    relation_repository: RelationRepositoryDep,
    entity_parser: EntityParserDep,
    file_service: FileServiceDep,
    link_resolver: LinkResolverDep,
    search_service: SearchServiceDep,
    app_config: AppConfigDep,
    conflict_service: ConflictServiceDep,
    schema_service: SchemaServiceDep,
    alias_repository: EntityAliasRepositoryDep,
) -> EntityService:
    """Create EntityService with repository."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        search_service=search_service,
        app_config=app_config,
        conflict_service=conflict_service,
        schema_service=schema_service,
        alias_repository=alias_repository,
    )


EntityServiceDep = Annotated[EntityService, Depends(get_entity_service)]


async def get_entity_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    entity_parser: EntityParserV2Dep,
    file_service: FileServiceV2Dep,
    link_resolver: LinkResolverV2Dep,
    search_service: SearchServiceV2Dep,
    app_config: AppConfigDep,
    conflict_service: ConflictServiceV2Dep,
    schema_service: SchemaServiceV2Dep,
    alias_repository: EntityAliasRepositoryV2Dep,
) -> EntityService:
    """Create EntityService for v2 API."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        search_service=search_service,
        app_config=app_config,
        conflict_service=conflict_service,
        schema_service=schema_service,
        alias_repository=alias_repository,
    )


EntityServiceV2Dep = Annotated[EntityService, Depends(get_entity_service_v2)]


async def get_entity_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    entity_parser: EntityParserV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
    conflict_service: ConflictServiceV2ExternalDep,
    schema_service: SchemaServiceV2ExternalDep,
    alias_repository: EntityAliasRepositoryV2ExternalDep,
) -> EntityService:
    """Create EntityService for v2 API (uses external_id)."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        search_service=search_service,
        app_config=app_config,
        conflict_service=conflict_service,
        schema_service=schema_service,
        alias_repository=alias_repository,
    )


EntityServiceV2ExternalDep = Annotated[EntityService, Depends(get_entity_service_v2_external)]


# --- Context Service ---


async def get_context_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    app_config: AppConfigDep,
) -> ContextService:
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=app_config,
    )


ContextServiceDep = Annotated[ContextService, Depends(get_context_service)]


async def get_context_service_v2(  # pragma: no cover
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    app_config: AppConfigDep,
) -> ContextService:
    """Create ContextService for v2 API."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=app_config,
    )


ContextServiceV2Dep = Annotated[ContextService, Depends(get_context_service_v2)]


async def get_context_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    app_config: AppConfigDep,
) -> ContextService:
    """Create ContextService for v2 API (uses external_id)."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=app_config,
    )


ContextServiceV2ExternalDep = Annotated[ContextService, Depends(get_context_service_v2_external)]


# --- Sync Service ---


async def get_sync_service(
    app_config: AppConfigDep,
    entity_service: EntityServiceDep,
    entity_parser: EntityParserDep,
    entity_repository: EntityRepositoryDep,
    relation_repository: RelationRepositoryDep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceDep,
    file_service: FileServiceDep,
) -> SyncService:  # pragma: no cover
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]


async def get_sync_service_v2(
    app_config: AppConfigDep,
    entity_service: EntityServiceV2Dep,
    entity_parser: EntityParserV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceV2Dep,
    file_service: FileServiceV2Dep,
) -> SyncService:  # pragma: no cover
    """Create SyncService for v2 API."""
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceV2Dep = Annotated[SyncService, Depends(get_sync_service_v2)]


async def get_sync_service_v2_external(
    app_config: AppConfigDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_parser: EntityParserV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> SyncService:  # pragma: no cover
    """Create SyncService for v2 API (uses external_id)."""
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceV2ExternalDep = Annotated[SyncService, Depends(get_sync_service_v2_external)]


# --- Background Task Scheduler ---


class TaskScheduler(Protocol):
    def schedule(self, task_name: str, **payload: Any) -> None:
        """Schedule a background task by name."""


def _log_task_failure(completed: asyncio.Task) -> None:
    try:
        completed.result()
    except Exception as exc:  # pragma: no cover
        logger.exception("Background task failed", error=str(exc))


class LocalTaskScheduler:
    """Default scheduler that runs tasks in-process via asyncio.create_task."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[..., Coroutine[Any, Any, None]]],
    ) -> None:
        self._handlers = handlers

    def schedule(self, task_name: str, **payload: Any) -> None:
        handler = self._handlers.get(task_name)
        # Trigger: task name is not registered
        # Why: avoid silently dropping background work
        # Outcome: fail fast to surface misconfiguration
        if not handler:
            raise ValueError(f"Unknown task name: {task_name}")
        task = asyncio.create_task(handler(**payload))
        task.add_done_callback(_log_task_failure)


async def get_task_scheduler(
    entity_service: EntityServiceV2ExternalDep,
    sync_service: SyncServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
) -> TaskScheduler:
    """Create a scheduler that maps task specs to coroutines."""

    async def _reindex_entity(
        entity_id: int,
        resolve_relations: bool = False,
        **_: Any,
    ) -> None:
        await entity_service.reindex_entity(entity_id)
        # Trigger: caller requests relation resolution
        # Why: resolve forward references created before the entity existed
        # Outcome: updates unresolved relations pointing to this entity
        if resolve_relations:
            await sync_service.resolve_relations(entity_id=entity_id)

    async def _resolve_relations(entity_id: int, **_: Any) -> None:
        await sync_service.resolve_relations(entity_id=entity_id)

    async def _sync_project(force_full: bool = False, **_: Any) -> None:
        await sync_service.sync(
            project_config.home,
            project_config.name,
            force_full=force_full,
        )

    async def _reindex_project(**_: Any) -> None:
        await search_service.reindex_all()

    return LocalTaskScheduler(
        {
            "reindex_entity": _reindex_entity,
            "resolve_relations": _resolve_relations,
            "sync_project": _sync_project,
            "reindex_project": _reindex_project,
        }
    )


TaskSchedulerDep = Annotated[TaskScheduler, Depends(get_task_scheduler)]


# --- Project Service ---


async def get_project_service(
    project_repository: ProjectRepositoryDep,
) -> ProjectService:
    """Create ProjectService with repository."""
    return ProjectService(repository=project_repository)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


# --- Directory Service ---


async def get_directory_service(
    entity_repository: EntityRepositoryDep,
) -> DirectoryService:
    """Create DirectoryService with dependencies."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceDep = Annotated[DirectoryService, Depends(get_directory_service)]


async def get_directory_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses integer project_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceV2Dep = Annotated[DirectoryService, Depends(get_directory_service_v2)]


async def get_directory_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses external_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceV2ExternalDep = Annotated[
    DirectoryService, Depends(get_directory_service_v2_external)
]


# --- CodeGraph (Tb G2) ---


async def get_codegraph_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    search_repository: SearchRepositoryV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
):
    """Create CodeGraphService for v2 API (external_id project path).

    The project name (from `ProjectConfigV2ExternalDep`) drives the `code://`
    permalinks; the integer project_id scopes the repository queries.
    """
    return CodeGraphService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        search_repository=search_repository,
        project_id=project_id,
        project_name=project_config.name,
        app_config=app_config,
    )


CodeGraphServiceV2ExternalDep = Annotated[
    CodeGraphService, Depends(get_codegraph_service_v2_external)
]


# --- Distillation (Tb G3 engine: scheduler singleton + per-project service) ---


# Process-wide singleton. The scheduler must survive across requests so its
# cadence counters accumulate; it is NOT rebuilt per request. The DistillationDispatcher
# it owns builds a fresh per-project DistillationService on each trigger (stateless).
_distillation_scheduler_singleton: DistillationScheduler | None = None
_distillation_init_lock: asyncio.Lock | None = None


async def _build_distillation_service_for_project(
    project_id: int,
) -> DistillationService | None:
    """Standalone per-project service factory for the dispatcher (background-task path).

    Runs outside a request (the scheduler dispatches from a fire-and-forget
    ``asyncio.create_task``), so it cannot use FastAPI DI. It resolves the project
    from its numeric id and builds the service via the standalone factory —
    reusing the app's existing DB session (``get_or_create_db`` is idempotent on
    the db_path). Failures are swallowed and logged so a distillation error can
    never propagate back through the scheduler into the write that triggered it.
    """
    try:
        from memopad import db
        from memopad.config import ConfigManager
        from memopad.repository.project_repository import ProjectRepository

        from memopad.services.distillation_factory import get_distillation_service

        app_cfg = ConfigManager().config
        _, session_maker = await db.get_or_create_db(
            db_path=app_cfg.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        project = await ProjectRepository(session_maker).get_by_id(project_id)
        if project is None:
            logger.warning(
                f"distillation trigger for unknown project_id={project_id}; skipping"
            )
            return None
        return await get_distillation_service(project, scheduler=_distillation_scheduler_singleton)
    except Exception as exc:  # pragma: no cover - degrade gracefully, never raise
        logger.error(f"distillation service build failed for project {project_id}: {exc}")
        return None


async def get_distillation_scheduler(app_config: AppConfigDep) -> DistillationScheduler:
    """Return the process-wide DistillationScheduler singleton.

    Constructed once (lock-guarded) with a ``DistillationDispatcher`` whose service
    factory is the standalone builder above. The dispatcher closes over the module
    global, so it reads the singleton lazily at trigger time (after this function has
    set it) — that breaks the scheduler↔dispatcher cycle cleanly.
    """
    global _distillation_scheduler_singleton, _distillation_init_lock
    if _distillation_scheduler_singleton is not None:
        return _distillation_scheduler_singleton
    if _distillation_init_lock is None:
        _distillation_init_lock = asyncio.Lock()
    async with _distillation_init_lock:
        if _distillation_scheduler_singleton is None:
            pipeline_config = PipelineConfig.from_app_config(app_config)
            dispatcher = DistillationDispatcher(_build_distillation_service_for_project)
            _distillation_scheduler_singleton = DistillationScheduler(
                pipeline_config, callback=dispatcher
            )
    return _distillation_scheduler_singleton


DistillationSchedulerDep = Annotated[
    DistillationScheduler, Depends(get_distillation_scheduler)
]


async def get_distillation_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
    app_config: AppConfigDep,
    session_maker: SessionMakerDep,
    scheduler: DistillationSchedulerDep,
) -> DistillationService:
    """Create DistillationService for v2 API (external_id project path).

    Used by the manual ``/distill`` endpoint and the ``/facts`` / ``/scenarios`` /
    ``/persona`` list endpoints. The automatic create-path trigger does NOT use this
    — it goes through the scheduler singleton + dispatcher (standalone factory) so
    distillation never blocks or fails the write.
    """
    embedding_service = EmbeddingService.maybe_create(session_maker, project_id)
    state_path = (
        Path(app_config.data_dir_path) / "distillation" / f"project-{project_id}-state.json"
    )
    return DistillationService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        entity_service=entity_service,
        app_config=app_config,
        project_id=project_id,
        scheduler=scheduler,
        embedding_service=embedding_service,
        state_path=state_path,
    )


DistillationServiceV2ExternalDep = Annotated[
    DistillationService, Depends(get_distillation_service_v2_external)
]
