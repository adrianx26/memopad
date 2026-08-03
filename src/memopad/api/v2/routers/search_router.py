"""V2 router for search operations.

This router uses external_id UUIDs for stable, API-friendly routing.
V1 uses string-based project names which are less efficient and less stable.
"""

from fastapi import APIRouter, Path

from memopad.api.v2.utils import to_search_results
from memopad.schemas.search import SearchQuery, SearchResponse, SemanticSearchQuery
from memopad.deps import (
    SearchServiceV2ExternalDep,
    EntityServiceV2ExternalDep,
    TaskSchedulerDep,
    ProjectExternalIdPathDep,
)
from memopad.deps.db import SessionMakerDep

# Note: No prefix here - it's added during registration as /v2/{project_id}/search
router = APIRouter(tags=["search"])


@router.post("/search/", response_model=SearchResponse)
async def search(
    query: SearchQuery,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    page: int = 1,
    page_size: int = 10,
):
    """Search across all knowledge and documents in a project.

    V2 uses external_id UUIDs for stable API references.

    Args:
        project_id: Project external UUID from URL path
        query: Search query parameters (text, filters, etc.)
        search_service: Search service scoped to project
        entity_service: Entity service scoped to project
        page: Page number for pagination
        page_size: Number of results per page

    Returns:
        SearchResponse with paginated search results
    """
    limit = page_size
    offset = (page - 1) * page_size
    results = await search_service.search(query, limit=limit, offset=offset)
    search_results = await to_search_results(entity_service, results)
    return SearchResponse(
        results=search_results,
        current_page=page,
        page_size=page_size,
    )


@router.post("/search/reindex")
async def reindex(
    task_scheduler: TaskSchedulerDep,
    project_id: ProjectExternalIdPathDep,
    force: bool = False,
):
    """Recreate and populate the search index for a project.

    This is a background operation. By default it is incremental: only
    changed/new entities are re-indexed and unchanged ones are skipped, so
    repeat calls don't redo the whole corpus. Pass ``force=true`` for a full
    wipe-and-rebuild — useful after bulk updates, schema changes, or if the
    index becomes corrupted.

    Args:
        project_id: Project external UUID from URL path
        task_scheduler: Task scheduler for background work
        force: When true, perform a full wipe-and-rebuild instead of an
            incremental reindex.

    Returns:
        Status message indicating reindex has been initiated
    """
    task_scheduler.schedule("reindex_project", project_id=project_id, force=force)
    return {"status": "ok", "message": "Reindex initiated"}


@router.post("/search/semantic", response_model=SearchResponse)
async def semantic_search(
    query: SemanticSearchQuery,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,  # internal numeric project id
):
    """Search using semantic embeddings and/or keyword search.

    Args:
        query: SemanticSearchQuery with query string and mode.
        search_service: Search service.
        entity_service: Entity service.
        session_maker: DB Session Maker.
        project_id: Internal Project ID resolved from URL external UUID.

    Returns:
        SearchResponse with ranked search results.
    """
    try:
        results = await search_service.hybrid_search(
            query_text=query.query,
            mode=query.mode,
            limit=query.limit,
            session_maker=session_maker,
            project_id=project_id,
        )
    except ValueError as e:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    search_results = await to_search_results(entity_service, results)
    return SearchResponse(
        results=search_results,
        current_page=1,
        page_size=query.limit,
    )
