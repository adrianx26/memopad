"""V2 Knowledge Router - External ID-based entity operations.

This router provides external_id (UUID) based CRUD operations for entities,
using stable string UUIDs that won't change with file moves or database migrations.

Key improvements:
- Stable external UUIDs that won't change with file moves or renames
- Better API ergonomics with consistent string identifiers
- Direct database lookups via unique indexed column
- Simplified caching strategies
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Response, Path, Query
from loguru import logger

from memopad.deps import (
    EntityServiceV2ExternalDep,
    SearchServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    ProjectConfigV2ExternalDep,
    AppConfigDep,
    EntityRepositoryV2ExternalDep,
    ProjectExternalIdPathDep,
    RelationRepositoryV2ExternalDep,
    TaskSchedulerDep,
    FileServiceV2ExternalDep,
    ObservationRepositoryV2ExternalDep,
    CodeGraphServiceV2ExternalDep,
    DistillationSchedulerDep,
    DistillationServiceV2ExternalDep,
)
from memopad.schemas import DeleteEntitiesResponse
from memopad.schemas.base import Entity
from memopad.schemas.request import EditEntityRequest
from memopad.schemas.v2 import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    MoveEntityRequestV2,
    MoveDirectoryRequestV2,
    DeleteDirectoryRequestV2,
)
from memopad.schemas.response import DirectoryMoveResult, DirectoryDeleteResult
from memopad.services.provenance_service import (
    DERIVED_FROM_RELATION_TYPE,
    build_drill_down_chain,
    entity_level,
    parse_source_entities,
    render_drill_down_chain,
)
from memopad.services.skill_service import (
    SKILL_ENTITY_TYPE,
    SKILL_STATUS_KEY,
    STATUS_VALIDATED,
    build_skill_detail,
    group_skill_observations,
    is_skill_entity,
    render_skill_detail,
    skill_status,
    skill_version,
    structural_validation,
)

# --- Distillation (Tb L0-L3) ---
from memopad.services.distillation_scheduler import is_pipeline_active
from memopad.services.distillation_service import (
    FACT_ENTITY_TYPE as DISTILL_FACT_TYPE,
    SCENARIO_ENTITY_TYPE as DISTILL_SCENARIO_TYPE,
    PERSONA_ENTITY_TYPE as DISTILL_PERSONA_TYPE,
)
import asyncio

router = APIRouter(prefix="/knowledge", tags=["knowledge-v2"])


def _schedule_distillation(scheduler, app_config, project_id: int) -> None:
    """Fire-and-forget an automatic distillation pass; never fails the write.

    Gated on `is_pipeline_active` (levels_enabled AND levels_pipeline_automatic) so
    turning the flag off disables auto-distillation even though the scheduler's own
    cadences may be non-zero. The scheduler dispatches fired triggers to the
    DistillationDispatcher, which builds a per-project service and swallows its own
    errors — so this only needs to guard against policy-layer failures.

    The trigger runs under the process-wide background-task semaphore (shared with
    the reindex scheduler) so a bulk write cannot pile up distillation tasks that
    starve the SQLite write lock and the connection pool.
    """
    if not is_pipeline_active(app_config):
        return

    from memopad.deps.services import get_background_task_semaphore

    semaphore = get_background_task_semaphore(app_config.background_task_concurrency)

    async def _run() -> None:
        async with semaphore:
            try:
                await scheduler.record_new_memory(project_id)
            except Exception as exc:  # pragma: no cover - never propagate to the caller
                logger.warning(f"distillation trigger failed for project {project_id}: {exc}")

    asyncio.create_task(_run())

## Resolution endpoint


@router.post("/resolve", response_model=EntityResolveResponse)
async def resolve_identifier(
    project_id: ProjectExternalIdPathDep,
    data: EntityResolveRequest,
    link_resolver: LinkResolverV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
) -> EntityResolveResponse:
    """Resolve a string identifier (external_id, permalink, title, or path) to entity info.

    This endpoint provides a bridge between v1-style identifiers and v2 external_ids.
    Use this to convert existing references to the new UUID-based format.

    Args:
        data: Request containing the identifier to resolve

    Returns:
        Entity external_id and metadata about how it was resolved

    Raises:
        HTTPException: 404 if identifier cannot be resolved

    Example:
        POST /v2/{project_id}/knowledge/resolve
        {"identifier": "specs/search"}

        Returns:
        {
            "external_id": "550e8400-e29b-41d4-a716-446655440000",
            "entity_id": 123,
            "permalink": "specs/search",
            "file_path": "specs/search.md",
            "title": "Search Specification",
            "resolution_method": "permalink"
        }
    """
    logger.info(f"API v2 request: resolve_identifier for '{data.identifier}'")

    # Try to resolve by external_id first
    entity = await entity_repository.get_by_external_id(data.identifier)
    resolution_method = "external_id" if entity else "search"

    # If not found by external_id, try other resolution methods
    # Pass source_path for context-aware resolution (prefers notes closer to source)
    # Pass strict to control fuzzy search fallback (default False allows fuzzy matching)
    if not entity:
        entity = await link_resolver.resolve_link(
            data.identifier, source_path=data.source_path, strict=data.strict
        )
        if entity:
            # Determine resolution method
            if entity.permalink == data.identifier:
                resolution_method = "permalink"
            elif entity.title == data.identifier:
                resolution_method = "title"
            elif entity.file_path == data.identifier:
                resolution_method = "path"
            else:
                resolution_method = "search"

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity not found: '{data.identifier}'")

    result = EntityResolveResponse(
        external_id=entity.external_id,
        entity_id=entity.id,
        permalink=entity.permalink,
        file_path=entity.file_path,
        title=entity.title,
        resolution_method=resolution_method,
    )

    logger.info(
        f"API v2 response: resolved '{data.identifier}' to external_id={result.external_id} via {resolution_method}"
    )

    return result


## Read endpoints


@router.get("/entities/{entity_id}", response_model=EntityResponseV2)
async def get_entity_by_id(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Get an entity by its external ID (UUID).

    This is the primary entity retrieval method in v2, using stable UUID
    identifiers that won't change with file moves.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Complete entity with observations and relations

    Raises:
        HTTPException: 404 if entity not found
    """
    logger.info(f"API v2 request: get_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if not entity:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

    result = EntityResponseV2.model_validate(entity)
    logger.info(f"API v2 response: external_id={entity_id}, title='{result.title}'")

    return result


## Create endpoints


@router.post("/entities", response_model=EntityResponseV2)
async def create_entity(
    project_id: ProjectExternalIdPathDep,
    data: Entity,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    scheduler: DistillationSchedulerDep,
    app_config: AppConfigDep,
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Create a new entity.

    Args:
        data: Entity data to create
        fast: If True, defer indexing to background tasks

    Returns:
        Created entity with generated external_id (UUID) and file content
    """
    logger.info(
        "API v2 request", endpoint="create_entity", entity_type=data.entity_type, title=data.title
    )

    if fast:
        entity = await entity_service.fast_write_entity(data)
        task_scheduler.schedule(
            "reindex_entity",
            entity_id=entity.id,
            project_id=project_id,
        )
    else:
        entity = await entity_service.create_entity(data)
        await search_service.index_entity(entity, background_tasks=background_tasks)

    result = EntityResponseV2.model_validate(entity)
    if fast:
        result = result.model_copy(update={"observations": [], "relations": []})

    # Always read and return file content
    content = await file_service.read_file_content(entity.file_path)
    result = result.model_copy(update={"content": content})

    # Tb L0-L3: nudge the automatic distillation pipeline (fire-and-forget).
    _schedule_distillation(scheduler, app_config, project_id)

    logger.info(
        f"API v2 response: endpoint='create_entity' external_id={entity.external_id}, title={result.title}, permalink={result.permalink}, status_code=201"
    )
    return result


## Update endpoints


@router.put("/entities/{entity_id}", response_model=EntityResponseV2)
async def update_entity_by_id(
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    scheduler: DistillationSchedulerDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Update an entity by external ID.

    If the entity doesn't exist, it will be created (upsert behavior).

    Args:
        entity_id: External ID (UUID string)
        data: Updated entity data
        fast: If True, defer indexing to background tasks

    Returns:
        Updated entity with file content
    """
    logger.info(f"API v2 request: update_entity_by_id entity_id={entity_id}")

    # Check if entity exists (external_id is the source of truth for v2)
    existing = await entity_repository.get_by_external_id(entity_id)
    created = existing is None

    if fast:
        entity = await entity_service.fast_write_entity(data, external_id=entity_id)
        response.status_code = 200 if existing else 201
        task_scheduler.schedule(
            "reindex_entity",
            entity_id=entity.id,
            project_id=project_id,
            resolve_relations=created,
        )
    else:
        if existing:
            # Update the existing entity in-place to avoid path-based duplication
            entity = await entity_service.update_entity(existing, data)
            response.status_code = 200
        else:
            # Create new entity, then bind external_id to the requested UUID
            entity = await entity_service.create_entity(data)
            if entity.external_id != entity_id:
                entity = await entity_repository.update(
                    entity.id,
                    {"external_id": entity_id},
                )
                if not entity:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Entity with external_id '{entity_id}' not found",
                    )
            response.status_code = 201

        await search_service.index_entity(entity, background_tasks=background_tasks)

    result = EntityResponseV2.model_validate(entity)
    if fast:
        result = result.model_copy(update={"observations": [], "relations": []})

    # Always read and return file content
    content = await file_service.read_file_content(entity.file_path)
    result = result.model_copy(update={"content": content})

    # Tb L0-L3: nudge the automatic distillation pipeline (fire-and-forget).
    _schedule_distillation(scheduler, app_config, project_id)

    logger.info(
        f"API v2 response: external_id={entity_id}, created={created}, status_code={response.status_code}"
    )
    return result


@router.patch("/entities/{entity_id}", response_model=EntityResponseV2)
async def edit_entity_by_id(
    data: EditEntityRequest,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    file_service: FileServiceV2ExternalDep,
    scheduler: DistillationSchedulerDep,
    app_config: AppConfigDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    fast: bool = Query(
        True, description="If true, write quickly and defer indexing to background tasks."
    ),
) -> EntityResponseV2:
    """Edit an existing entity by external ID using operations like append, prepend, etc.

    Args:
        entity_id: External ID (UUID string)
        data: Edit operation details
        fast: If True, defer indexing to background tasks

    Returns:
        Updated entity with file content

    Raises:
        HTTPException: 404 if entity not found, 400 if edit fails
    """
    logger.info(
        f"API v2 request: edit_entity_by_id entity_id={entity_id}, operation='{data.operation}'"
    )

    # Verify entity exists
    entity = await entity_repository.get_by_external_id(entity_id)
    if not entity:  # pragma: no cover
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

    try:
        if fast:
            updated_entity = await entity_service.fast_edit_entity(
                entity=entity,
                operation=data.operation,
                content=data.content,
                section=data.section,
                find_text=data.find_text,
                expected_replacements=data.expected_replacements,
            )
            task_scheduler.schedule(
                "reindex_entity",
                entity_id=updated_entity.id,
                project_id=project_id,
            )
        else:
            # Edit using the entity's permalink or path
            identifier = entity.permalink or entity.file_path
            updated_entity = await entity_service.edit_entity(
                identifier=identifier,
                operation=data.operation,
                content=data.content,
                section=data.section,
                find_text=data.find_text,
                expected_replacements=data.expected_replacements,
            )

            await search_service.index_entity(updated_entity, background_tasks=background_tasks)

        result = EntityResponseV2.model_validate(updated_entity)
        if fast:
            result = result.model_copy(update={"observations": [], "relations": []})

        # Always read and return file content
        content = await file_service.read_file_content(updated_entity.file_path)
        result = result.model_copy(update={"content": content})

        # Tb L0-L3: nudge the automatic distillation pipeline (fire-and-forget).
        _schedule_distillation(scheduler, app_config, project_id)

        logger.info(
            f"API v2 response: external_id={entity_id}, operation='{data.operation}', status_code=200"
        )

        return result

    except Exception as e:
        logger.error(f"Error editing entity {entity_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Delete endpoints


@router.delete("/entities/{entity_id}", response_model=DeleteEntitiesResponse)
async def delete_entity_by_id(
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    search_service=Depends(lambda: None),  # Optional for now
) -> DeleteEntitiesResponse:
    """Delete an entity by external ID.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Deletion status

    Note: Returns deleted=False if entity doesn't exist (idempotent)
    """
    logger.info(f"API v2 request: delete_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if entity is None:
        logger.info(f"API v2 response: external_id={entity_id} not found, deleted=False")
        return DeleteEntitiesResponse(deleted=False)

    # Delete the entity using internal ID
    deleted = await entity_service.delete_entity(entity.id)

    # Remove from search index if search service available
    if search_service:
        background_tasks.add_task(search_service.handle_delete, entity)  # pragma: no cover

    logger.info(f"API v2 response: external_id={entity_id}, deleted={deleted}")

    return DeleteEntitiesResponse(deleted=deleted)


## Move endpoint


@router.put("/entities/{entity_id}/move", response_model=EntityResponseV2)
async def move_entity(
    data: MoveEntityRequestV2,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Move an entity to a new file location.

    V2 API uses external_id (UUID) in the URL path for stable references.
    The external_id will remain stable after the move.

    Args:
        project_id: Project external ID from URL path
        entity_id: Entity external ID from URL path (primary identifier)
        data: Move request with destination path only

    Returns:
        Updated entity with new file path
    """
    logger.info(
        f"API v2 request: move_entity entity_id={entity_id}, destination='{data.destination_path}'"
    )

    try:
        # First, get the entity by external_id to verify it exists
        entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:  # pragma: no cover
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

        # Move the entity using its current file path as identifier
        moved_entity = await entity_service.move_entity(
            identifier=entity.file_path,  # Use file path for resolution
            destination_path=data.destination_path,
            project_config=project_config,
            app_config=app_config,
        )

        # Reindex at new location
        reindexed_entity = await entity_service.link_resolver.resolve_link(data.destination_path)
        if reindexed_entity:
            await search_service.index_entity(reindexed_entity, background_tasks=background_tasks)

        result = EntityResponseV2.model_validate(moved_entity)

        logger.info(f"API v2 response: moved external_id={entity_id} to '{data.destination_path}'")

        return result

    except HTTPException:  # pragma: no cover
        raise  # pragma: no cover
    except Exception as e:
        logger.error(f"Error moving entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Move directory endpoint


@router.post("/move-directory", response_model=DirectoryMoveResult)
async def move_directory(
    data: MoveDirectoryRequestV2,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
) -> DirectoryMoveResult:
    """Move all entities in a directory to a new location.

    V2 API uses project external_id in the URL path for stable references.
    Moves all files within a source directory to a destination directory,
    updating database records and optionally updating permalinks.

    Args:
        project_id: Project external ID from URL path
        data: Move request with source and destination directories

    Returns:
        DirectoryMoveResult with counts and details of moved files
    """
    logger.info(
        f"API v2 request: move_directory source='{data.source_directory}', destination='{data.destination_directory}'"
    )

    try:
        # Move the directory using the service
        result = await entity_service.move_directory(
            source_directory=data.source_directory,
            destination_directory=data.destination_directory,
            project_config=project_config,
            app_config=app_config,
        )

        # Reindex moved entities
        for file_path in result.moved_files:
            entity = await entity_service.link_resolver.resolve_link(file_path)
            if entity:
                await search_service.index_entity(entity, background_tasks=background_tasks)

        logger.info(
            f"API v2 response: move_directory "
            f"total={result.total_files}, success={result.successful_moves}, failed={result.failed_moves}"
        )
        return result

    except Exception as e:
        logger.error(f"Error moving directory: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Delete directory endpoint


@router.post("/delete-directory", response_model=DirectoryDeleteResult)
async def delete_directory(
    data: DeleteDirectoryRequestV2,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
) -> DirectoryDeleteResult:
    """Delete all entities in a directory.

    V2 API uses project external_id in the URL path for stable references.
    Deletes all files within a directory, updating database records and
    removing files from the filesystem.

    Args:
        project_id: Project external ID from URL path
        data: Delete request with directory path

    Returns:
        DirectoryDeleteResult with counts and details of deleted files
    """
    logger.info(f"API v2 request: delete_directory directory='{data.directory}'")

    try:
        # Delete the directory using the service
        result = await entity_service.delete_directory(
            directory=data.directory,
        )

        logger.info(
            f"API v2 response: delete_directory "
            f"total={result.total_files}, success={result.successful_deletes}, failed={result.failed_deletes}"
        )
        return result

    except Exception as e:
        logger.error(f"Error deleting directory: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Backlinks endpoint


@router.get("/entities/{entity_id}/backlinks")
async def get_backlinks(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Target entity external ID (UUID)"),
) -> dict:
    """Return all relations pointing TO the given entity.

    Includes both resolved backlinks (relations whose to_id matches the target)
    and unresolved ones (wikilinks like [[target]] that haven't been resolved yet
    but match the target's permalink/title slug).

    Args:
        entity_id: External ID of the target entity.

    Returns:
        Dict with `backlinks`: list of {from_external_id, from_title, from_permalink,
        relation_type, context, resolved}.
    """
    logger.info(f"API v2 request: get_backlinks entity_id={entity_id}")

    target = await entity_repository.get_by_external_id(entity_id)
    if not target:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

    aliases: list[str] = []
    if target.permalink:
        aliases.append(target.permalink)
    if target.title and target.title not in aliases:
        aliases.append(target.title)

    relations = await relation_repository.find_backlinks(target.id, aliases)

    items: list[dict] = []
    for rel in relations:
        from_entity = rel.from_entity
        # from_entity is None only for unresolved relations whose source was
        # also deleted — extremely rare but possible during half-completed
        # sync. Skip those rows rather than emitting null-everything entries.
        if from_entity is None:
            continue
        items.append(
            {
                "from_external_id": from_entity.external_id,
                "from_title": from_entity.title,
                "from_permalink": from_entity.permalink,
                "relation_type": rel.relation_type,
                "context": rel.context,
                "resolved": rel.to_id is not None,
            }
        )

    logger.info(
        f"API v2 response: get_backlinks entity_id={entity_id} count={len(items)}"
    )
    return {"target_external_id": entity_id, "target_title": target.title, "backlinks": items}


## Drill-down (provenance chain) endpoint


@router.get("/entities/{entity_id}/drill-down")
async def get_drill_down(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID) to trace from"),
    target_level: str = Query("L0", description="Stop descending at this level (L0|L1|L2|L3)"),
    max_depth: int = Query(5, description="Safety bound on recursion depth", ge=1, le=10),
) -> dict:
    """Trace a distilled memory back to its ground-truth sources (Tb G5).

    Follows, in order:
      1. frontmatter `source_entities` (the plan's authoritative provenance field,
         stored in `entity_metadata`), and
      2. native `derived_from` relations as a secondary backref path,
    recursing down to `target_level` (default L0) or until a source cannot be
    resolved.

    This is the reversible side of the L0–L3 distillation pyramid: from any
    high-level abstraction (L3 persona, L2 scenario, L1 fact) the caller can reach
    the raw L0 evidence it was distilled from, plus the file_path of each hop so
    bodies can be read via `read_note`.

    Returns a dict with `chain` (rendered Markdown), `target`, and `nodes`
    (structured tree). Unresolved sources appear as leaves tagged _[unresolved]_.
    """
    logger.info(
        f"API v2 request: get_drill_down entity_id={entity_id} target_level={target_level}"
    )

    target = await entity_repository.get_by_external_id(entity_id)
    if not target:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

    chain_root = await build_drill_down_chain(
        entity_repository,
        relation_repository,
        target,
        target_level=target_level,
        max_depth=max_depth,
    )

    def _serialize(node) -> dict:
        return {
            "external_id": node.external_id,
            "title": node.title,
            "permalink": node.permalink,
            "level": node.level,
            "file_path": node.file_path,
            "resolved": node.resolved,
            "via": node.via,
            "source_ref": node.source_ref,
            "children": [_serialize(c) for c in node.children],
        }

    logger.info(
        f"API v2 response: get_drill_down entity_id={entity_id} "
        f"target_level={target_level} source_refs={len(parse_source_entities(target.entity_metadata))}"
    )
    return {
        "target_external_id": entity_id,
        "target_title": target.title,
        "target_level": entity_level(target.entity_metadata),
        "source_entities": parse_source_entities(target.entity_metadata),
        "derived_from_relation_type": DERIVED_FROM_RELATION_TYPE,
        "chain": render_drill_down_chain(chain_root),
        "nodes": _serialize(chain_root),
    }


## Skill asset endpoints (Tb G1)
#
# Skills are entities with `entity_type = "skill"` carrying versioning metadata
# (`skill_version`, `skill_status`) in `entity_metadata` and structured
# `[trigger]` / `[step]` / `[validation]` observations. Create/get reuse the
# generic entity endpoints; these two endpoints cover the skill-specific
# operations the generic CRUD can't do: list-by-type and structural validation
# with status promotion. Gated behind `skills_enabled` (default off).


@router.get("/skills")
async def list_skills(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    app_config: AppConfigDep,
    status: str | None = Query(
        None, description="Filter by skill_status: draft | validated | deprecated"
    ),
    limit: int = Query(50, ge=1, le=200, description="Max skills to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict:
    """List skill entities, optionally filtered by `skill_status`.

    Returns summary dicts (external_id, title, permalink, file_path, version,
    status, source count). Use `GET /knowledge/entities/{id}` for full detail.
    """
    if not app_config.skills_enabled:
        raise HTTPException(
            status_code=400,
            detail="Skill asset is disabled (skills_enabled=false). "
            "Enable MEMOPAD_SKILLS_ENABLED to use skill endpoints.",
        )

    if status and status not in ("draft", "validated", "deprecated"):
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of draft|validated|deprecated, got '{status}'",
        )

    logger.info(f"API v2 request: list_skills status={status} limit={limit} offset={offset}")

    skills = await entity_repository.list_by_entity_type(
        SKILL_ENTITY_TYPE, limit=limit if not status else 200, offset=offset
    )

    summaries = []
    for ent in skills:
        st = skill_status(ent.entity_metadata)
        if status and st != status:
            continue
        summaries.append(
            {
                "external_id": ent.external_id,
                "title": ent.title,
                "permalink": ent.permalink,
                "file_path": ent.file_path,
                "skill_version": skill_version(ent.entity_metadata),
                "skill_status": st,
                "source_entities_count": len((ent.entity_metadata or {}).get("source_entities", []) or []),
            }
        )

    logger.info(f"API v2 response: list_skills returned {len(summaries)} skills")
    return {"skills": summaries, "count": len(summaries)}


@router.post("/skills/{entity_id}/validate")
async def validate_skill(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    app_config: AppConfigDep,
    background_tasks: BackgroundTasks,
    entity_id: str = Path(..., description="Skill entity external ID (UUID)"),
) -> dict:
    """Structurally validate a skill and, if complete, promote it to `validated`.

    The Tb triple must be present: >= 1 `[trigger]`, >= 1 `[step]`,
    >= 1 `[validation]` observation. When complete, the skill's frontmatter
    `skill_status` is set to `validated` (files remain the source of truth: the
    markdown is rewritten via `entity_service.update_entity` and re-indexed).

    LLM verification (do the steps actually cover the trigger?) is deferred —
    structural completeness is the deterministic precondition recorded here.
    """
    if not app_config.skills_enabled:
        raise HTTPException(
            status_code=400,
            detail="Skill asset is disabled (skills_enabled=false).",
        )

    logger.info(f"API v2 request: validate_skill entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if not entity:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )
    if not is_skill_entity(entity):
        raise HTTPException(
            status_code=400,
            detail=f"Entity '{entity_id}' is not a skill (entity_type='{entity.entity_type}').",
        )

    observations = await observation_repository.find_by_entity(entity.id)
    grouped = group_skill_observations(observations)
    result = structural_validation(grouped)

    if not result.ok:
        # Validation failed — do not promote. Return 200 with ok=False so the
        # caller gets the missing-category list (not a transport error).
        return {
            "external_id": entity.external_id,
            "ok": False,
            "missing": result.missing,
            "present": result.present,
            "skill_status": skill_status(entity.entity_metadata),
        }

    # --- Structural check passed: promote skill_status -> validated in the file ---
    import frontmatter as _frontmatter
    from pathlib import Path as _Path

    existing_content = await file_service.read_file_content(_Path(entity.file_path))
    post = _frontmatter.loads(existing_content)
    post[SKILL_STATUS_KEY] = STATUS_VALIDATED
    new_content = _frontmatter.dumps(post)

    # Reuse the canonical update path so the file is rewritten, re-parsed, and
    # entity_metadata is re-derived from frontmatter (files = source of truth).
    directory = _Path(entity.file_path).parent.as_posix()
    merged_metadata = {**(entity.entity_metadata or {}), SKILL_STATUS_KEY: STATUS_VALIDATED}

    from memopad.schemas.base import Entity as EntitySchema

    schema = EntitySchema(
        title=entity.title,
        directory=directory,
        entity_type=SKILL_ENTITY_TYPE,
        content_type=entity.content_type or "text/markdown",
        content=new_content,
        entity_metadata=merged_metadata,
    )
    updated = await entity_service.update_entity(entity, schema)
    await search_service.index_entity(updated, background_tasks=background_tasks)

    logger.info(
        f"API v2 response: validate_skill entity_id={entity_id} promoted to validated "
        f"(version={skill_version(updated.entity_metadata)})"
    )
    return {
        "external_id": updated.external_id,
        "ok": True,
        "missing": [],
        "present": result.present,
        "skill_status": STATUS_VALIDATED,
    }


## CodeGraph endpoints (Tb G2)
# Index source code into the existing Entity/Relation graph and query it. Gated
# behind `codegraph_enabled` (default off). `impacts` is derived (reverse of
# `calls`) at query time, never stored.


@router.post("/codegraph/index")
async def index_codegraph(
    codegraph_service: CodeGraphServiceV2ExternalDep,
    app_config: AppConfigDep,
    root: str = Query(..., description="Absolute or project-relative path of the source tree to index"),
    languages: list[str] | None = Query(
        None, description="Languages to index (default: python)"
    ),
) -> dict:
    """Parse a source tree and upsert code entities + relations + search rows.

    Idempotent: re-running on the same tree upserts (file_path is the conflict
    key) and replaces outgoing relations + search rows.
    """
    if not app_config.codegraph_enabled:
        raise HTTPException(
            status_code=400,
            detail="CodeGraph is disabled (codegraph_enabled=false). "
            "Enable MEMOPAD_CODEGRAPH_ENABLED to index code.",
        )
    from pathlib import Path as _Path

    logger.info(f"API v2 request: index_codegraph root={root} languages={languages}")
    try:
        report = await codegraph_service.index_directory(
            _Path(root), languages=set(languages) if languages else None
        )
    except Exception as e:  # CodeGraphError or filesystem issues
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "files": report.files,
        "entities": report.entities,
        "relations": report.relations,
        "skipped": report.skipped,
        "summary": report.render(),
    }


@router.get("/codegraph/find-symbol")
async def find_symbol(
    codegraph_service: CodeGraphServiceV2ExternalDep,
    app_config: AppConfigDep,
    name: str = Query(..., description="Symbol name (substring, case-insensitive)"),
    exact: bool = Query(False, description="Require an exact title match"),
) -> dict:
    """Find functions/classes/modules by name."""
    if not app_config.codegraph_enabled:
        raise HTTPException(status_code=400, detail="CodeGraph is disabled (codegraph_enabled=false).")
    hits = await codegraph_service.find_symbol(name, exact=exact)
    return {
        "symbols": [
            {
                "permalink": h.permalink,
                "title": h.title,
                "entity_type": h.entity_type,
                "qualified_name": h.qualified_name,
                "file": h.file,
            }
            for h in hits
        ],
        "count": len(hits),
    }


@router.get("/codegraph/impact-path")
async def impact_path(
    codegraph_service: CodeGraphServiceV2ExternalDep,
    app_config: AppConfigDep,
    permalink: str = Query(..., description="code:// permalink of the symbol being changed"),
    max_hops: int = Query(5, ge=1, le=20, description="Max BFS hops"),
) -> dict:
    """BFS over reverse `calls`: what does changing this symbol affect?"""
    if not app_config.codegraph_enabled:
        raise HTTPException(status_code=400, detail="CodeGraph is disabled (codegraph_enabled=false).")
    view = await codegraph_service._load_graph_view()
    if permalink not in view.symbols:
        raise HTTPException(status_code=404, detail=f"No code symbol at '{permalink}'.")
    ip = await codegraph_service.impact_path(permalink, max_hops=max_hops)
    return {
        "root": permalink,
        "impacted": [
            {"permalink": p, "hops": ip.distances[p]}
            for p in sorted(ip.distances, key=lambda p: (ip.distances[p], p))
        ],
        "count": len(ip.distances),
        "render": ip.render(view),
    }


@router.get("/codegraph/context")
async def code_context(
    codegraph_service: CodeGraphServiceV2ExternalDep,
    app_config: AppConfigDep,
    permalink: str = Query(..., description="code:// permalink of the symbol"),
    max_tokens: int = Query(0, ge=0, description="Token budget for the rendered context (0=unlimited)"),
) -> dict:
    """Definition + direct dependencies + direct callers for a symbol."""
    if not app_config.codegraph_enabled:
        raise HTTPException(status_code=400, detail="CodeGraph is disabled (codegraph_enabled=false).")
    view = await codegraph_service._load_graph_view()
    if permalink not in view.symbols:
        raise HTTPException(status_code=404, detail=f"No code symbol at '{permalink}'.")
    ctx = await codegraph_service.code_context(permalink, max_tokens=max_tokens)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"No code symbol at '{permalink}'.")
    return {
        "permalink": ctx.symbol.permalink,
        "title": ctx.symbol.title,
        "entity_type": ctx.symbol.entity_type,
        "defined_in": ctx.defined_in,
        "callers": ctx.callers,
        "callees": ctx.callees,
        "imports": ctx.imports,
        "render": ctx.render(view, max_tokens=max_tokens),
    }


## Distillation endpoints (Tb L0-L3)


@router.post("/distill")
async def distill_memory(
    project_id: ProjectExternalIdPathDep,
    service: DistillationServiceV2ExternalDep,
    level: str = Query(
        "L1",
        description="Comma-separated distillation levels to run (L1, L2, L3). "
        "L1 distils new L0 observations into atomic facts; L2 clusters facts into "
        "scenarios; L3 aggregates stable facts into the persona.",
    ),
    max_memories: int = Query(50, ge=1, le=1000, description="Max L0 entities to scan per L1 pass."),
) -> dict:
    """Manually trigger a distillation pass (bypasses the automatic cadence).

    Runs synchronously and returns per-level counts. The automatic create-path
    trigger is fire-and-forget; this endpoint is for on-demand / debug / CLI use.
    """
    levels = {part.strip().upper() for part in level.split(",") if part.strip()}
    unknown = levels - {"L1", "L2", "L3"}
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown level(s): {sorted(unknown)}. Use L1, L2, L3."
        )
    summary: dict = {"project_id": project_id, "levels": sorted(levels)}
    if "L1" in levels:
        summary["l1_facts"] = await service.run_l1_pass(max_memories=max_memories)
    if "L2" in levels:
        summary["l2_scenarios"] = await service.run_l2_pass()
    if "L3" in levels:
        summary["l3_persona"] = await service.run_l3_pass()
    return summary


@router.get("/facts", response_model=list[EntityResponseV2])
async def list_facts(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    limit: int = Query(200, ge=1, le=1000, description="Max facts to return."),
) -> list[EntityResponseV2]:
    """List distilled L1 atomic facts (entity_type=fact)."""
    facts = await entity_repository.list_by_entity_type(DISTILL_FACT_TYPE, limit=limit)
    return [EntityResponseV2.model_validate(f) for f in facts]


@router.get("/scenarios", response_model=list[EntityResponseV2])
async def list_scenarios(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    limit: int = Query(200, ge=1, le=1000, description="Max scenarios to return."),
) -> list[EntityResponseV2]:
    """List distilled L2 scenarios (entity_type=scenario)."""
    scenarios = await entity_repository.list_by_entity_type(DISTILL_SCENARIO_TYPE, limit=limit)
    return [EntityResponseV2.model_validate(s) for s in scenarios]


@router.get("/persona", response_model=EntityResponseV2)
async def get_persona(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
) -> EntityResponseV2:
    """Get the distilled L3 persona for this project (one per project).

    Raises 404 if no persona has been distilled yet.
    """
    personas = await entity_repository.list_by_entity_type(DISTILL_PERSONA_TYPE, limit=1)
    if not personas:
        raise HTTPException(
            status_code=404,
            detail="No persona has been distilled yet. Run a distillation pass with level=L3.",
        )
    return EntityResponseV2.model_validate(personas[0])
