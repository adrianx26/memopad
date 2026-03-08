"""Stoolap-native project repository.

Handles CRUD for the ``project`` table when the Stoolap backend is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class StoolapProject:
    """Lightweight project representation mirroring the ORM Project model."""

    id: int
    name: str
    path: str
    permalink: Optional[str] = None
    is_active: bool = True
    is_default: Optional[bool] = None


def _row_to_project(row: Any) -> StoolapProject:
    """Convert a Stoolap result row to StoolapProject."""
    return StoolapProject(
        id=row[0],
        name=row[1],
        path=row[2],
        permalink=row[3],
        is_active=bool(row[4]),
        is_default=bool(row[5]) if row[5] is not None else None,
    )


_SELECT_COLS = "id, name, path, permalink, is_active, is_default"


class StoolapProjectRepository:
    """Project repository backed by Stoolap AsyncDatabase.

    Args:
        db: An open ``stoolap.AsyncDatabase`` instance.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def get_by_name(self, name: str) -> Optional[StoolapProject]:
        """Fetch a project by name."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM project WHERE name = ?",
            [name],
        )
        return _row_to_project(rows[0]) if rows else None

    async def get_by_permalink(self, permalink: str) -> Optional[StoolapProject]:
        """Fetch a project by permalink."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM project WHERE permalink = ?",
            [permalink],
        )
        return _row_to_project(rows[0]) if rows else None

    async def get_default_project(self) -> Optional[StoolapProject]:
        """Return the project marked is_default=1, or None."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM project WHERE is_default = 1 LIMIT 1",
            [],
        )
        return _row_to_project(rows[0]) if rows else None

    async def get_active_projects(self) -> List[StoolapProject]:
        """Return all active projects."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM project WHERE is_active = 1",
            [],
        )
        return [_row_to_project(r) for r in rows]

    async def list_projects(self) -> List[StoolapProject]:
        """Return all projects."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM project ORDER BY name",
            [],
        )
        return [_row_to_project(r) for r in rows]

    async def get_or_create(self, name: str, path: str, permalink: Optional[str] = None) -> StoolapProject:
        """Fetch an existing project or create it if it does not exist."""
        existing = await self.get_by_name(name)
        if existing:
            return existing

        await self._db.execute(
            "INSERT INTO project (name, path, permalink, is_active) VALUES (?, ?, ?, 1)",
            [name, path, permalink or name.lower().replace(" ", "-")],
        )
        logger.info(f"Stoolap: created project '{name}' at {path}")
        created = await self.get_by_name(name)
        if created is None:  # pragma: no cover
            raise RuntimeError(f"Failed to create project '{name}'")
        return created

    async def create(self, data: Dict[str, Any]) -> StoolapProject:
        """Insert a new project row from a dict of fields."""
        await self._db.execute(
            "INSERT INTO project (name, path, permalink, is_active, is_default) VALUES (?, ?, ?, ?, ?)",
            [
                data["name"],
                data["path"],
                data.get("permalink", data["name"].lower().replace(" ", "-")),
                int(data.get("is_active", True)),
                1 if data.get("is_default") else None,
            ],
        )
        result = await self.get_by_name(data["name"])
        if result is None:  # pragma: no cover
            raise RuntimeError(f"Failed to create project: {data['name']}")
        return result

    async def update_path(self, project_id: int, new_path: str) -> None:
        """Update the filesystem path of a project."""
        await self._db.execute(
            "UPDATE project SET path = ? WHERE id = ?",
            [new_path, project_id],
        )

    async def set_as_default(self, project_id: int) -> None:
        """Clear all defaults and mark the given project as default."""
        await self._db.execute("UPDATE project SET is_default = NULL", [])
        await self._db.execute(
            "UPDATE project SET is_default = 1 WHERE id = ?",
            [project_id],
        )

    async def delete(self, project_id: int) -> None:
        """Delete a project (cascades to entities, observations, relations)."""
        await self._db.execute("DELETE FROM project WHERE id = ?", [project_id])
        logger.info(f"Stoolap: deleted project id={project_id}")
