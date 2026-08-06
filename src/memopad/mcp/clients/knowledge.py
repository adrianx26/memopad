"""Typed client for knowledge/entity API operations.

Encapsulates all /v2/projects/{project_id}/knowledge/* endpoints.
"""

from typing import Any

from httpx import AsyncClient

from memopad.mcp.tools.utils import call_get, call_post, call_put, call_patch, call_delete
from memopad.schemas.response import (
    EntityResponse,
    DeleteEntitiesResponse,
    DirectoryMoveResult,
    DirectoryDeleteResult,
)


class KnowledgeClient:
    """Typed client for knowledge graph entity operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/knowledge/*
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = KnowledgeClient(http_client, project_id)
            entity = await client.create_entity(entity_data)
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the knowledge client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/knowledge"

    # --- Entity CRUD Operations ---

    async def create_entity(
        self, entity_data: dict[str, Any], *, fast: bool | None = None
    ) -> EntityResponse:
        """Create a new entity.

        Args:
            entity_data: Entity data including title, content, folder, etc.

        Returns:
            EntityResponse with created entity details

        Raises:
            ToolError: If the request fails
        """
        params = {"fast": fast} if fast is not None else None
        response = await call_post(
            self.http_client,
            f"{self._base_path}/entities",
            json=entity_data,
            params=params,
        )
        return EntityResponse.model_validate(response.json())

    async def update_entity(
        self,
        entity_id: str,
        entity_data: dict[str, Any],
        *,
        fast: bool | None = None,
    ) -> EntityResponse:
        """Update an existing entity (full replacement).

        Args:
            entity_id: Entity external_id (UUID)
            entity_data: Complete entity data for replacement

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        params = {"fast": fast} if fast is not None else None
        response = await call_put(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
            json=entity_data,
            params=params,
        )
        return EntityResponse.model_validate(response.json())

    async def get_entity(self, entity_id: str) -> EntityResponse:
        """Get an entity by ID.

        Args:
            entity_id: Entity external_id (UUID)

        Returns:
            EntityResponse with entity details

        Raises:
            ToolError: If the entity is not found or request fails
        """
        response = await call_get(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
        )
        return EntityResponse.model_validate(response.json())

    async def patch_entity(
        self,
        entity_id: str,
        patch_data: dict[str, Any],
        *,
        fast: bool | None = None,
    ) -> EntityResponse:
        """Partially update an entity.

        Args:
            entity_id: Entity external_id (UUID)
            patch_data: Partial entity data to update

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        params = {"fast": fast} if fast is not None else None
        response = await call_patch(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
            json=patch_data,
            params=params,
        )
        return EntityResponse.model_validate(response.json())

    async def delete_entity(self, entity_id: str) -> DeleteEntitiesResponse:
        """Delete an entity.

        Args:
            entity_id: Entity external_id (UUID)

        Returns:
            DeleteEntitiesResponse confirming deletion

        Raises:
            ToolError: If the entity is not found or request fails
        """
        response = await call_delete(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
        )
        return DeleteEntitiesResponse.model_validate(response.json())

    async def move_entity(self, entity_id: str, destination_path: str) -> EntityResponse:
        """Move an entity to a new location.

        Args:
            entity_id: Entity external_id (UUID)
            destination_path: New file path for the entity

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        response = await call_put(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}/move",
            json={"destination_path": destination_path},
        )
        return EntityResponse.model_validate(response.json())

    async def move_directory(
        self, source_directory: str, destination_directory: str
    ) -> DirectoryMoveResult:
        """Move all entities in a directory to a new location.

        Args:
            source_directory: Source directory path (relative to project root)
            destination_directory: Destination directory path (relative to project root)

        Returns:
            DirectoryMoveResult with counts and details of moved files

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            f"{self._base_path}/move-directory",
            json={
                "source_directory": source_directory,
                "destination_directory": destination_directory,
            },
        )
        return DirectoryMoveResult.model_validate(response.json())

    async def delete_directory(self, directory: str) -> DirectoryDeleteResult:
        """Delete all entities in a directory.

        Args:
            directory: Directory path to delete (relative to project root)

        Returns:
            DirectoryDeleteResult with counts and details of deleted files

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            f"{self._base_path}/delete-directory",
            json={"directory": directory},
        )
        return DirectoryDeleteResult.model_validate(response.json())

    # --- Backlinks ---

    async def get_backlinks(self, entity_id: str) -> dict:
        """Get all relations pointing TO the given entity.

        Args:
            entity_id: Target entity external_id (UUID).

        Returns:
            Dict with target_external_id, target_title, and backlinks list.
        """
        response = await call_get(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}/backlinks",
        )
        return response.json()

    async def drill_down(
        self,
        entity_id: str,
        *,
        target_level: str = "L0",
        max_depth: int = 5,
    ) -> dict:
        """Trace a distilled memory back to its ground-truth sources (Tb G5).

        Args:
            entity_id: Entity external_id (UUID) to trace from.
            target_level: Stop descending at this level (L0|L1|L2|L3). Default L0.
            max_depth: Safety bound on recursion depth (1–10). Default 5.

        Returns:
            Dict with `chain` (rendered Markdown), `nodes` (structured tree),
            `target_*` metadata, and `source_entities`.
        """
        params = {"target_level": target_level, "max_depth": max_depth}
        response = await call_get(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}/drill-down",
            params=params,
        )
        return response.json()

    # --- Skill asset (Tb G1) ---

    async def list_skills(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict:
        """List skill entities, optionally filtered by `skill_status`.

        Args:
            status: Optional `draft` | `validated` | `deprecated` filter.
            limit/offset: Pagination.

        Returns:
            Dict with `skills` (list of summary dicts) and `count`.
        """
        params: dict = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        response = await call_get(self.http_client, f"{self._base_path}/skills", params=params)
        return response.json()

    async def validate_skill(self, entity_id: str) -> dict:
        """Structurally validate a skill and set its status to `validated`.

        Args:
            entity_id: Skill entity external_id (UUID).

        Returns:
            Dict with `ok`, `missing`, `present`, `skill_status`, and the
            updated entity `external_id`. Raises ToolError on 4xx.
        """
        response = await call_post(
            self.http_client, f"{self._base_path}/skills/{entity_id}/validate"
        )
        return response.json()

    # --- CodeGraph (Tb G2) ---

    async def index_codegraph(
        self, root: str, *, languages: list[str] | None = None
    ) -> dict:
        """Index a source tree into the code graph (entities + relations + search).

        Args:
            root: Absolute or project-relative path of the source tree.
            languages: Optional language list (default: python).

        Returns:
            Dict with `files`, `entities`, `relations`, `skipped`, `summary`.
        """
        params: dict = {"root": root}
        if languages:
            params["languages"] = languages
        response = await call_post(
            self.http_client, f"{self._base_path}/codegraph/index", params=params
        )
        return response.json()

    async def find_symbol(
        self, name: str, *, exact: bool = False
    ) -> dict:
        """Find code symbols (functions/classes/modules) by name.

        Returns:
            Dict with `symbols` (permalink, title, entity_type, qualified_name)
            and `count`.
        """
        params = {"name": name, "exact": str(exact).lower()}
        response = await call_get(
            self.http_client, f"{self._base_path}/codegraph/find-symbol", params=params
        )
        return response.json()

    async def impact_path(
        self, permalink: str, *, max_hops: int = 5
    ) -> dict:
        """BFS over reverse `calls`: what does changing `permalink` affect?

        Returns:
            Dict with `root`, `impacted` (list of {permalink, hops}), `count`,
            and `render` (markdown).
        """
        params = {"permalink": permalink, "max_hops": max_hops}
        response = await call_get(
            self.http_client, f"{self._base_path}/codegraph/impact-path", params=params
        )
        return response.json()

    async def code_context(
        self, permalink: str, *, max_tokens: int = 0
    ) -> dict:
        """Definition + direct dependencies + direct callers for a symbol.

        Returns:
            Dict with `permalink`, `title`, `defined_in`, `callers`, `callees`,
            `imports`, and `render` (markdown).
        """
        params = {"permalink": permalink, "max_tokens": max_tokens}
        response = await call_get(
            self.http_client, f"{self._base_path}/codegraph/context", params=params
        )
        return response.json()

    # --- Resolution ---

    async def resolve_entity(self, identifier: str) -> str:
        """Resolve a string identifier to an entity external_id.

        Args:
            identifier: The identifier to resolve (permalink, title, or path)

        Returns:
            The resolved entity external_id (UUID)

        Raises:
            ToolError: If the identifier cannot be resolved
        """
        response = await call_post(
            self.http_client,
            f"{self._base_path}/resolve",
            json={"identifier": identifier},
        )
        data = response.json()
        return data["external_id"]
