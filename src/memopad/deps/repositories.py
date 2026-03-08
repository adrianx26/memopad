"""Repository dependency injection for memopad.

This module provides repository dependencies:
- EntityRepository
- ObservationRepository
- RelationRepository
- SearchRepository

Each repository is scoped to a project ID from the request.
"""

from typing import Annotated, Union

from fastapi import Depends

from memopad.config import ConfigManager, DatabaseBackend
from memopad.deps.db import SessionMakerDep
from memopad.deps.projects import (
    ProjectIdDep,
    ProjectIdPathDep,
    ProjectExternalIdPathDep,
)
from memopad.repository.entity_repository import EntityRepository
from memopad.repository.observation_repository import ObservationRepository
from memopad.repository.relation_repository import RelationRepository
from memopad.repository.search_repository import SearchRepository, create_search_repository


def _is_stoolap() -> bool:
    """Return True when the configured backend is Stoolap."""
    return ConfigManager().config.database_backend == DatabaseBackend.STOOLAP


def _get_stoolap_db_sync():
    """Return the cached Stoolap AsyncDatabase (must already be initialised)."""
    from memopad.db import _stoolap_db  # noqa: PLC0415
    if _stoolap_db is None:  # pragma: no cover
        raise RuntimeError(
            "Stoolap database is not initialised. "
            "Ensure get_stoolap_db() is awaited at server startup."
        )
    return _stoolap_db


# --- Entity Repository ---


async def get_entity_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> EntityRepository:
    """Create an EntityRepository (or Stoolap equivalent) for the current project."""
    if _is_stoolap():  # pragma: no cover
        from memopad.repository.stoolap_entity_repository import StoolapEntityRepository  # noqa: PLC0415
        return StoolapEntityRepository(_get_stoolap_db_sync(), project_id=project_id)  # type: ignore[return-value]
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryDep = Annotated[EntityRepository, Depends(get_entity_repository)]


async def get_entity_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses integer project_id from path)."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryV2Dep = Annotated[EntityRepository, Depends(get_entity_repository_v2)]


async def get_entity_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses external_id from path)."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryV2ExternalDep = Annotated[
    EntityRepository, Depends(get_entity_repository_v2_external)
]


# --- Observation Repository ---


async def get_observation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for the current project."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryDep = Annotated[ObservationRepository, Depends(get_observation_repository)]


async def get_observation_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryV2Dep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2)
]


async def get_observation_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API (uses external_id)."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryV2ExternalDep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2_external)
]


# --- Relation Repository ---


async def get_relation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> RelationRepository:
    """Create a RelationRepository instance for the current project."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryDep = Annotated[RelationRepository, Depends(get_relation_repository)]


async def get_relation_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryV2Dep = Annotated[RelationRepository, Depends(get_relation_repository_v2)]


async def get_relation_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API (uses external_id)."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryV2ExternalDep = Annotated[
    RelationRepository, Depends(get_relation_repository_v2_external)
]


# --- Search Repository ---


async def get_search_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> SearchRepository:
    """Create a backend-specific SearchRepository instance for the current project.

    Uses factory function to return SQLiteSearchRepository or PostgresSearchRepository
    based on database backend configuration.
    """
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryDep = Annotated[SearchRepository, Depends(get_search_repository)]


async def get_search_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> SearchRepository:
    """Create a SearchRepository instance for v2 API."""
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryV2Dep = Annotated[SearchRepository, Depends(get_search_repository_v2)]


async def get_search_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> SearchRepository:
    """Create a SearchRepository instance for v2 API (uses external_id)."""
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryV2ExternalDep = Annotated[
    SearchRepository, Depends(get_search_repository_v2_external)
]
