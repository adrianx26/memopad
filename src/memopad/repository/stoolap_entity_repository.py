"""Stoolap-native entity repository.

Mirrors the public interface of ``EntityRepository`` but uses
``stoolap.AsyncDatabase`` raw SQL instead of SQLAlchemy ORM.
Returns lightweight ``StoolapEntity`` dataclasses so the service
layer can access attributes in the same way as ORM objects.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass  # stoolap imported lazily to avoid hard import at module level


# ---------------------------------------------------------------------------
# Lightweight entity dataclass — replaces the SQLAlchemy ORM Entity model
# ---------------------------------------------------------------------------

@dataclass
class StoolapEntity:
    """Lightweight entity representation returned by StoolapEntityRepository."""

    id: int
    external_id: str
    title: str
    entity_type: str
    content_type: str
    project_id: int
    file_path: str
    permalink: Optional[str] = None
    entity_metadata: Optional[str] = None
    checksum: Optional[str] = None
    mtime: Optional[float] = None
    size: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    observations: List[Any] = field(default_factory=list)
    relations: List[Any] = field(default_factory=list)

    @property
    def metadata_dict(self) -> Dict[str, Any]:
        """Parse entity_metadata JSON string to dict."""
        if not self.entity_metadata:
            return {}
        try:
            return json.loads(self.entity_metadata)
        except (json.JSONDecodeError, TypeError):
            return {}


def _row_to_entity(row: Any) -> StoolapEntity:
    """Convert a Stoolap result row (dict) to a StoolapEntity."""
    return StoolapEntity(
        id=row.get("id"),
        external_id=row.get("external_id"),
        title=row.get("title"),
        entity_type=row.get("entity_type"),
        entity_metadata=row.get("entity_metadata"),
        content_type=row.get("content_type"),
        project_id=row.get("project_id"),
        permalink=row.get("permalink"),
        file_path=row.get("file_path"),
        checksum=row.get("checksum"),
        mtime=row.get("mtime"),
        size=row.get("size"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


_SELECT_COLS = (
    "id, external_id, title, entity_type, entity_metadata, content_type, "
    "project_id, permalink, file_path, checksum, mtime, size, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class StoolapEntityRepository:
    """Entity repository backed by Stoolap AsyncDatabase.

    Args:
        db: An open ``stoolap.AsyncDatabase`` instance.
        project_id: The project scope for all queries.
    """

    def __init__(self, db: Any, project_id: int) -> None:
        self._db = db
        self._project_id = project_id

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: int) -> Optional[StoolapEntity]:
        """Fetch entity by primary key."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM entity WHERE id = ? AND project_id = ?",
            [entity_id, self._project_id],
        )
        return _row_to_entity(rows[0]) if rows else None

    async def get_by_external_id(self, external_id: str) -> Optional[StoolapEntity]:
        """Fetch entity by external (UUID) ID."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM entity WHERE external_id = ? AND project_id = ?",
            [external_id, self._project_id],
        )
        return _row_to_entity(rows[0]) if rows else None

    async def get_by_permalink(self, permalink: str) -> Optional[StoolapEntity]:
        """Fetch entity by permalink."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM entity WHERE permalink = ? AND project_id = ?",
            [permalink, self._project_id],
        )
        return _row_to_entity(rows[0]) if rows else None

    async def get_by_file_path(self, file_path: str) -> Optional[StoolapEntity]:
        """Fetch entity by file path within the current project."""
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM entity WHERE file_path = ? AND project_id = ?",
            [file_path, self._project_id],
        )
        return _row_to_entity(rows[0]) if rows else None

    async def get_all_file_paths(self) -> List[str]:
        """Return all file paths for the current project."""
        rows = await self._db.query(
            "SELECT file_path FROM entity WHERE project_id = ?",
            [self._project_id],
        )
        return [r["file_path"] for r in rows]

    async def find_by_directory_prefix(self, prefix: str) -> List[StoolapEntity]:
        """Fetch all entities whose file path starts with *prefix*."""
        like = f"'{prefix.replace(chr(39), chr(39)+chr(39))}%'"
        rows = await self._db.query(
            f"SELECT {_SELECT_COLS} FROM entity WHERE file_path LIKE {like} AND project_id = ?",
            [self._project_id],
        )
        return [_row_to_entity(r) for r in rows]

    async def find_all(
        self,
        limit: int = 100,
        offset: int = 0,
        entity_type: Optional[str] = None,
    ) -> List[StoolapEntity]:
        """List entities for the current project with optional type filter."""
        if entity_type:
            rows = await self._db.query(
                f"SELECT {_SELECT_COLS} FROM entity "
                "WHERE project_id = ? AND entity_type = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [self._project_id, entity_type, limit, offset],
            )
        else:
            rows = await self._db.query(
                f"SELECT {_SELECT_COLS} FROM entity "
                "WHERE project_id = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [self._project_id, limit, offset],
            )
        return [_row_to_entity(r) for r in rows]

    async def count(self, entity_type: Optional[str] = None) -> int:
        """Count entities in the current project."""
        if entity_type:
            rows = await self._db.query(
                "SELECT COUNT(*) as c FROM entity WHERE project_id = ? AND entity_type = ?",
                [self._project_id, entity_type],
            )
        else:
            rows = await self._db.query(
                "SELECT COUNT(*) as c FROM entity WHERE project_id = ?",
                [self._project_id],
            )
        return rows[0]["c"] if rows else 0

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert_entity(self, data: Dict[str, Any]) -> StoolapEntity:
        """Insert or update an entity using file_path + project_id as the key.

        If an entity with the same file_path/project_id exists it is updated
        in-place; otherwise a new row is inserted.

        Args:
            data: Dict of entity fields. ``project_id`` is always overridden
                  with the repository's own project_id.

        Returns:
            The resulting StoolapEntity (fetched after upsert).
        """
        data = dict(data)
        data["project_id"] = self._project_id

        # Ensure external_id exists
        if not data.get("external_id"):
            data["external_id"] = str(uuid.uuid4())

        # Serialise metadata dict to JSON if needed
        if isinstance(data.get("entity_metadata"), dict):
            data["entity_metadata"] = json.dumps(data["entity_metadata"])

        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        data["updated_at"] = now

        existing = await self.get_by_file_path(data["file_path"])

        if existing:
            # UPDATE
            await self._db.execute(
                """
                UPDATE entity SET
                    external_id    = ?,
                    title          = ?,
                    entity_type    = ?,
                    entity_metadata = ?,
                    content_type   = ?,
                    permalink      = ?,
                    checksum       = ?,
                    mtime          = ?,
                    size           = ?,
                    updated_at     = ?
                WHERE file_path = ? AND project_id = ?
                """,
                [
                    data.get("external_id", existing.external_id),
                    data.get("title", existing.title),
                    data.get("entity_type", existing.entity_type),
                    data.get("entity_metadata", existing.entity_metadata),
                    data.get("content_type", existing.content_type),
                    data.get("permalink", existing.permalink),
                    data.get("checksum", existing.checksum),
                    data.get("mtime", existing.mtime),
                    data.get("size", existing.size),
                    now,
                    data["file_path"],
                    self._project_id,
                ],
            )
            logger.debug(f"Stoolap entity updated: {data['file_path']}")
        else:
            # INSERT
            await self._db.execute(
                """
                INSERT INTO entity
                    (external_id, title, entity_type, entity_metadata, content_type,
                     project_id, permalink, file_path, checksum, mtime, size,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    data["external_id"],
                    data["title"],
                    data.get("entity_type", "note"),
                    data.get("entity_metadata"),
                    data.get("content_type", "text/markdown"),
                    self._project_id,
                    data.get("permalink"),
                    data["file_path"],
                    data.get("checksum"),
                    data.get("mtime"),
                    data.get("size"),
                    data["created_at"],
                    data["updated_at"],
                ],
            )
            logger.debug(f"Stoolap entity inserted: {data['file_path']}")

        result = await self.get_by_file_path(data["file_path"])
        if result is None:  # pragma: no cover
            raise RuntimeError(f"Upsert failed — entity not found after write: {data['file_path']}")
        return result

    async def delete_by_file_path(self, file_path: str) -> bool:
        """Delete an entity by file path. Returns True if a row was deleted."""
        existing = await self.get_by_file_path(file_path)
        if not existing:
            return False
        await self._db.execute(
            "DELETE FROM entity WHERE file_path = ? AND project_id = ?",
            [file_path, self._project_id],
        )
        logger.debug(f"Stoolap entity deleted: {file_path}")
        return True

    async def delete_by_id(self, entity_id: int) -> bool:
        """Delete an entity by primary key."""
        existing = await self.get_by_id(entity_id)
        if not existing:
            return False
        await self._db.execute(
            "DELETE FROM entity WHERE id = ? AND project_id = ?",
            [entity_id, self._project_id],
        )
        return True
