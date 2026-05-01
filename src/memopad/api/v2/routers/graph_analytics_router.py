"""V2 Graph Analytics Router.

Three read-only endpoints over the relation graph:
  - GET /graph/clusters      — Louvain community detection
  - GET /graph/hubs          — degree-centrality ranked hubs
  - GET /graph/path          — shortest path between two entities

All operate on the same project's data and produce JSON-friendly results
(no SQLAlchemy objects, no NetworkX objects in the response).
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from memopad.deps import (
    EntityRepositoryV2ExternalDep,
    ProjectExternalIdPathDep,
)
from memopad.deps.db import SessionMakerDep
from memopad.services.graph_analytics_service import GraphAnalyticsService


router = APIRouter(prefix="/graph", tags=["graph-analytics"])


@router.get("/clusters")
async def get_clusters(
    project_id: ProjectExternalIdPathDep,
    session_maker: SessionMakerDep,
    min_size: int = Query(3, ge=1, le=1000, description="Minimum cluster size"),
) -> dict:
    """Run Louvain community detection on the project's relation graph.

    Returns clusters sorted descending by size. Each cluster is labeled by
    its highest-degree member's title.
    """
    logger.info(f"API v2 graph: get_clusters project_id={project_id} min_size={min_size}")
    service = GraphAnalyticsService(session_maker, project_id)
    clusters = await service.find_clusters(min_size=min_size)
    return {
        "project_id": project_id,
        "min_size": min_size,
        "cluster_count": len(clusters),
        "clusters": [asdict(c) for c in clusters],
    }


@router.get("/hubs")
async def get_hubs(
    project_id: ProjectExternalIdPathDep,
    session_maker: SessionMakerDep,
    top: int = Query(10, ge=1, le=200, description="Maximum hubs to return"),
) -> dict:
    """Top-N entities by total degree (incoming + outgoing relations)."""
    logger.info(f"API v2 graph: get_hubs project_id={project_id} top={top}")
    service = GraphAnalyticsService(session_maker, project_id)
    hubs = await service.find_hubs(top=top)
    return {
        "project_id": project_id,
        "top": top,
        "hubs": [asdict(h) for h in hubs],
    }


@router.get("/path")
async def get_path(
    project_id: ProjectExternalIdPathDep,
    session_maker: SessionMakerDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    from_id: str = Query(..., description="Source entity external_id (UUID)"),
    to_id: str = Query(..., description="Target entity external_id (UUID)"),
    max_length: int = Query(6, ge=1, le=20, description="Reject longer paths"),
) -> dict:
    """Shortest path between two entities through the relation graph.

    Accepts external_ids (UUIDs) so callers don't need to know internal
    integer ids. Returns hops in source→target order.
    """
    logger.info(
        f"API v2 graph: get_path project_id={project_id} "
        f"from_id={from_id} to_id={to_id} max_length={max_length}"
    )

    # Resolve the UUID external_ids to internal integer ids that the service
    # operates on. We do this here so the service stays focused on the graph
    # and doesn't need to know about the v2-vs-v1 id duality.
    source = await entity_repository.get_by_external_id(from_id)
    if not source:
        raise HTTPException(
            status_code=404, detail=f"Source entity '{from_id}' not found"
        )
    target = await entity_repository.get_by_external_id(to_id)
    if not target:
        raise HTTPException(
            status_code=404, detail=f"Target entity '{to_id}' not found"
        )

    service = GraphAnalyticsService(session_maker, project_id)
    result = await service.find_path(source.id, target.id, max_length=max_length)
    return {
        "project_id": project_id,
        "from_external_id": from_id,
        "to_external_id": to_id,
        "max_length": max_length,
        **asdict(result),
    }
