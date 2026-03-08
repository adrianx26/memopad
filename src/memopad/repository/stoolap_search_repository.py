"""Stoolap-native search repository.

Implements the same public search interface as ``SQLiteSearchRepository``
(and ``PostgresSearchRepository``) but executes queries against the
``search_index`` table in a Stoolap ``AsyncDatabase``.

Text matching uses SQL ``LIKE '%term%'`` clauses — each token from the
query is AND-ed together across the ``title``, ``content_stems``, and
``content_snippet`` columns.  This is semantically equivalent to the
FTS5 ``bm25`` search in the SQLite backend for small/medium datasets.

A future phase can add a Stoolap HNSW index on an embedding column to
replace LIKE-based search with actual vector-ANN retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger


# ---------------------------------------------------------------------------
# Result dataclass — mirrors SearchIndexRow used in SQLiteSearchRepository
# ---------------------------------------------------------------------------

@dataclass
class StoolapSearchResult:
    """A single result row from the Stoolap search index."""

    id: int
    entity_id: Optional[int]
    project_id: int
    title: Optional[str] = None
    permalink: Optional[str] = None
    file_path: Optional[str] = None
    type: Optional[str] = None
    content_snippet: Optional[str] = None
    metadata: Optional[str] = None
    category: Optional[str] = None
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    relation_type: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    score: float = 1.0  # placeholder — no bm25 in LIKE mode


_SELECT_COLS = (
    "id, entity_id, project_id, title, permalink, file_path, type, "
    "content_snippet, metadata, category, from_id, to_id, relation_type, "
    "created_at, updated_at"
)


def _row_to_result(row: Any) -> StoolapSearchResult:
    return StoolapSearchResult(
        id=row.get("id"),
        entity_id=row.get("entity_id"),
        project_id=row.get("project_id"),
        title=row.get("title"),
        permalink=row.get("permalink"),
        file_path=row.get("file_path"),
        type=row.get("type"),
        content_snippet=row.get("content_snippet"),
        metadata=row.get("metadata"),
        category=row.get("category"),
        from_id=row.get("from_id"),
        to_id=row.get("to_id"),
        relation_type=row.get("relation_type"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


def _tokenise(query: str) -> List[str]:
    """Split a user query into non-empty tokens, stripping quotes/wildcards."""
    raw = re.sub(r'[*"\'()]+', " ", query)
    return [t.strip() for t in raw.split() if t.strip() and len(t) > 1]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class StoolapSearchRepository:
    """Full-text search repository backed by Stoolap AsyncDatabase.

    Args:
        db: An open ``stoolap.AsyncDatabase`` instance.
        project_id: The project scope for all queries.
    """

    def __init__(self, db: Any, project_id: int) -> None:
        self._db = db
        self._project_id = project_id

    async def init_search_index(self) -> None:
        """No-op: schema is created at startup via stoolap_schema.py DDL."""
        logger.debug("StoolapSearchRepository: search_index table already created by DDL")

    async def search(
        self,
        query: str,
        *,
        types: Optional[Sequence[str]] = None,
        permalink: Optional[str] = None,
        after: Optional[datetime] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[StoolapSearchResult]:
        """Search the index returning up to *limit* results.

        Args:
            query: Free-text search query. Tokens are AND-ed with LIKE.
            types: Optional list of entity types to restrict results to.
            permalink: Optional exact permalink filter.
            after: Optional datetime — only return entries updated after this.
            limit: Max number of results to return.
            offset: Pagination offset.

        Returns:
            List of ``StoolapSearchResult`` objects sorted by updated_at DESC.
        """
        params: List[Any] = [self._project_id]
        clauses: List[str] = ["project_id = ?"]

        # Full-text matching: AND all tokens against title + content_stems + snippet
        tokens = _tokenise(query)
        for token in tokens:
            like = f"'%{token.lower().replace(chr(39), chr(39)+chr(39))}%'"
            clauses.append(
                f"(LOWER(title) LIKE {like} OR LOWER(content_stems) LIKE {like} OR LOWER(content_snippet) LIKE {like})"
            )

        # Type filter
        if types:
            placeholders = ", ".join("?" * len(types))
            clauses.append(f"type IN ({placeholders})")
            params.extend(types)

        # Permalink filter
        if permalink:
            clauses.append("permalink = ?")
            params.append(permalink)

        # Date filter
        if after:
            clauses.append("updated_at >= ?")
            params.append(after.isoformat())

        where = " AND ".join(clauses)
        sql = (
            f"SELECT {_SELECT_COLS} FROM search_index "
            f"WHERE {where} "
            f"ORDER BY updated_at DESC "
            f"LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        logger.debug(f"Stoolap search: tokens={tokens} types={types} limit={limit}")
        rows = await self._db.query(sql, params)
        return [_row_to_result(r) for r in rows]

    # ------------------------------------------------------------------
    # Index write operations (called from sync / entity service)
    # ------------------------------------------------------------------

    async def index_entity(self, entity: Any, content: Optional[str] = None) -> None:
        """Add or update a search index entry for an entity.

        Args:
            entity: A StoolapEntity (or any object with the same attributes).
            content: Optional raw content for the content_stems/snippet columns.
        """
        snippet = (content or "")[:500]
        stems = (content or "")

        existing_rows = await self._db.query(
            "SELECT id FROM search_index WHERE entity_id = ? AND project_id = ?",
            [entity.id, self._project_id],
        )

        now = datetime.now(timezone.utc).isoformat()

        if existing_rows:
            await self._db.execute(
                """
                UPDATE search_index SET
                    title           = ?,
                    permalink       = ?,
                    file_path       = ?,
                    type            = ?,
                    content_stems   = ?,
                    content_snippet = ?,
                    metadata        = ?,
                    updated_at      = ?
                WHERE entity_id = ? AND project_id = ?
                """,
                [
                    entity.title,
                    entity.permalink,
                    entity.file_path,
                    entity.entity_type,
                    stems,
                    snippet,
                    entity.entity_metadata,
                    now,
                    entity.id,
                    self._project_id,
                ],
            )
        else:
            await self._db.execute(
                """
                INSERT INTO search_index
                    (entity_id, project_id, title, permalink, file_path, type,
                     content_stems, content_snippet, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    entity.id,
                    self._project_id,
                    entity.title,
                    entity.permalink,
                    entity.file_path,
                    entity.entity_type,
                    stems,
                    snippet,
                    entity.entity_metadata,
                    now,
                    now,
                ],
            )

    async def remove_entity(self, entity_id: int) -> None:
        """Remove index entries for the given entity ID."""
        await self._db.execute(
            "DELETE FROM search_index WHERE entity_id = ? AND project_id = ?",
            [entity_id, self._project_id],
        )

    async def index_relation(self, relation: Any) -> None:
        """Add a relation to the search index (for relation-type results)."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO search_index
                (project_id, type, from_id, to_id, relation_type, created_at, updated_at)
            VALUES (?, 'relation', ?, ?, ?, ?, ?)
            """,
            [
                self._project_id,
                relation.from_id,
                relation.to_id,
                relation.relation_type,
                now,
                now,
            ],
        )

    async def count(self, query: str = "") -> int:
        """Return the count of matching search results."""
        params: List[Any] = [self._project_id]
        clauses = ["project_id = ?"]

        for token in _tokenise(query):
            like = f"'%{token.lower().replace(chr(39), chr(39)+chr(39))}%'"
            clauses.append(
                f"(LOWER(title) LIKE {like} OR LOWER(content_stems) LIKE {like} OR LOWER(content_snippet) LIKE {like})"
            )

        where = " AND ".join(clauses)
        rows = await self._db.query(
            f"SELECT COUNT(*) as c FROM search_index WHERE {where}", params
        )
        return rows[0]["c"] if rows else 0
