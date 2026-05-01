"""Typed client for graph analytics API operations.

Encapsulates /v2/projects/{project_id}/graph/* endpoints.
"""

from httpx import AsyncClient

from memopad.mcp.tools.utils import call_get


class GraphAnalyticsClient:
    """Read-only graph operations: clusters, hubs, shortest paths."""

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize with HTTP client and project external_id."""
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/graph"

    async def get_clusters(self, min_size: int = 3) -> dict:
        """Louvain community detection. Returns clusters sorted by size desc."""
        response = await call_get(
            self.http_client,
            f"{self._base_path}/clusters",
            params={"min_size": min_size},
        )
        return response.json()

    async def get_hubs(self, top: int = 10) -> dict:
        """Top-N entities by total degree."""
        response = await call_get(
            self.http_client,
            f"{self._base_path}/hubs",
            params={"top": top},
        )
        return response.json()

    async def get_path(
        self, from_external_id: str, to_external_id: str, max_length: int = 6
    ) -> dict:
        """Shortest path between two entities through the relation graph.

        Args:
            from_external_id: Source entity external_id (UUID).
            to_external_id: Target entity external_id (UUID).
            max_length: Maximum hops to consider.
        """
        response = await call_get(
            self.http_client,
            f"{self._base_path}/path",
            params={
                "from_id": from_external_id,
                "to_id": to_external_id,
                "max_length": max_length,
            },
        )
        return response.json()
