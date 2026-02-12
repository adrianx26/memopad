"""Knowledge graph schema exports.

This module exports all schema classes to simplify imports.
Rather than importing from individual schema files, you can
import everything from memopad.schemas.
"""

# Base types and models
from memopad.schemas.base import (
    Observation,
    EntityType,
    RelationType,
    Relation,
    Entity,
)

# Delete operation models
from memopad.schemas.delete import (
    DeleteEntitiesRequest,
)

# Request models
from memopad.schemas.request import (
    SearchNodesRequest,
    GetEntitiesRequest,
    CreateRelationsRequest,
)

# Response models
from memopad.schemas.response import (
    SQLAlchemyModel,
    ObservationResponse,
    RelationResponse,
    EntityResponse,
    EntityListResponse,
    SearchNodesResponse,
    DeleteEntitiesResponse,
)

from memopad.schemas.project_info import (
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
    ProjectInfoResponse,
)

from memopad.schemas.directory import (
    DirectoryNode,
)

from memopad.schemas.sync_report import (
    SyncReportResponse,
)

# For convenient imports, export all models
__all__ = [
    # Base
    "Observation",
    "EntityType",
    "RelationType",
    "Relation",
    "Entity",
    # Requests
    "SearchNodesRequest",
    "GetEntitiesRequest",
    "CreateRelationsRequest",
    # Responses
    "SQLAlchemyModel",
    "ObservationResponse",
    "RelationResponse",
    "EntityResponse",
    "EntityListResponse",
    "SearchNodesResponse",
    "DeleteEntitiesResponse",
    # Delete Operations
    "DeleteEntitiesRequest",
    # Project Info
    "ProjectStatistics",
    "ActivityMetrics",
    "SystemStatus",
    "ProjectInfoResponse",
    # Directory
    "DirectoryNode",
    # Sync
    "SyncReportResponse",
]
