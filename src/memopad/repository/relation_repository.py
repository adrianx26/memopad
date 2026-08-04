"""Repository for managing Relation objects."""

from typing import Sequence, List, Optional, Any, cast

from sqlalchemy import and_, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.orm.interfaces import LoaderOption

from memopad import db
from memopad.models import Relation, Entity
from memopad.repository.repository import Repository


class RelationRepository(Repository[Relation]):
    """Repository for Relation model with memory-specific operations."""

    def __init__(self, session_maker: async_sessionmaker, project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by
        """
        super().__init__(session_maker, Relation, project_id=project_id)

    async def find_relation(
        self, from_permalink: str, to_permalink: str, relation_type: str
    ) -> Optional[Relation]:
        """Find a relation by its from and to path IDs."""
        from_entity = aliased(Entity)
        to_entity = aliased(Entity)

        query = (
            select(Relation)
            .join(from_entity, Relation.from_id == from_entity.id)
            .join(to_entity, Relation.to_id == to_entity.id)
            .where(
                and_(
                    from_entity.permalink == from_permalink,
                    to_entity.permalink == to_permalink,
                    Relation.relation_type == relation_type,
                )
            )
        )
        return await self.find_one(query)

    async def find_by_entities(self, from_id: int, to_id: int) -> Sequence[Relation]:
        """Find all relations between two entities."""
        query = select(Relation).where((Relation.from_id == from_id) & (Relation.to_id == to_id))
        result = await self.execute_query(query)
        return result.scalars().all()

    async def find_by_type(self, relation_type: str) -> Sequence[Relation]:
        """Find all relations of a specific type."""
        query = select(Relation).filter(Relation.relation_type == relation_type)
        result = await self.execute_query(query)
        return result.scalars().all()

    async def delete_outgoing_relations_from_entity(self, entity_id: int) -> None:
        """Delete outgoing relations for an entity.

        Only deletes relations where this entity is the source (from_id),
        as these are the ones owned by this entity's markdown file.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(delete(Relation).where(Relation.from_id == entity_id))

    async def replace_outgoing_relations(
        self, entity_id: int, relations: List[Relation]
    ) -> int:
        """Atomically replace an entity's outgoing relations in one transaction.

        Deletes existing outgoing relations (``from_id == entity_id``) and inserts
        the new ones in a single session/commit, ignoring duplicates via ON
        CONFLICT DO NOTHING. Returns the number of relations actually inserted.

        Replaces the old separate-session ``delete_outgoing_relations_from_entity``
        + ``add_all`` pair, which committed the delete before the add and could
        leave the entity with zero outgoing relations if the add then raised.
        """
        values = [
            {
                "project_id": r.project_id if r.project_id else self.project_id,
                "from_id": r.from_id,
                "to_id": r.to_id,
                "to_name": r.to_name,
                "relation_type": r.relation_type,
                "context": r.context,
            }
            for r in relations
        ]
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(delete(Relation).where(Relation.from_id == entity_id))
            if not values:
                return 0
            dialect_name = session.bind.dialect.name if session.bind else "sqlite"
            if dialect_name == "postgresql":  # pragma: no cover
                stmt = (  # pragma: no cover
                    pg_insert(Relation)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(Relation.id)
                )
                result = await session.execute(stmt)  # pragma: no cover
                return len(result.fetchall())  # pragma: no cover
            else:
                stmt = sqlite_insert(Relation).values(values)
                stmt = stmt.on_conflict_do_nothing()
                result = cast(CursorResult[Any], await session.execute(stmt))
                return result.rowcount if result.rowcount > 0 else 0

    async def find_unresolved_relations(self) -> Sequence[Relation]:
        """Find all unresolved relations, where to_id is null."""
        query = select(Relation).filter(Relation.to_id.is_(None))
        result = await self.execute_query(query)
        return result.scalars().all()

    async def find_backlinks(
        self,
        target_entity_id: int,
        target_permalinks: Sequence[str] = (),
    ) -> Sequence[Relation]:
        """Find all relations pointing TO the target entity.

        Returns both resolved backlinks (to_id == target_entity_id) and unresolved
        backlinks (to_id IS NULL AND to_name matches one of target_permalinks).

        Including unresolved is important: when a note references [[Coffee Brewing]]
        before that note exists, the relation is created with to_id=NULL and
        to_name="coffee-brewing". If we only checked to_id we'd miss those.

        Args:
            target_entity_id: The id of the entity being linked to.
            target_permalinks: Aliases (permalink, title slug) that may appear
                               in unresolved relations' to_name field.

        Returns:
            Sequence of Relation objects with from_entity eagerly loaded.
        """
        conditions: list[Any] = [Relation.to_id == target_entity_id]
        if target_permalinks:
            conditions.append(
                and_(Relation.to_id.is_(None), Relation.to_name.in_(list(target_permalinks)))
            )

        from sqlalchemy import or_

        query = (
            select(Relation)
            .options(selectinload(Relation.from_entity))
            .where(or_(*conditions))
        )
        result = await self.execute_query(query)
        return result.scalars().all()

    async def find_unresolved_relations_for_entity(self, entity_id: int) -> Sequence[Relation]:
        """Find unresolved relations for a specific entity.

        Args:
            entity_id: The entity whose unresolved outgoing relations to find.

        Returns:
            List of unresolved relations where this entity is the source.
        """
        query = select(Relation).filter(Relation.from_id == entity_id, Relation.to_id.is_(None))
        result = await self.execute_query(query)
        return result.scalars().all()

    async def add_all_ignore_duplicates(self, relations: List[Relation]) -> int:
        """Bulk insert relations, ignoring duplicates.

        Uses ON CONFLICT DO NOTHING to skip relations that would violate the
        unique constraint on (from_id, to_name, relation_type). This is useful
        for bulk operations where the same link may appear multiple times in
        a document.

        Works with both SQLite and PostgreSQL dialects.

        Args:
            relations: List of Relation objects to insert

        Returns:
            Number of relations actually inserted (excludes duplicates)
        """
        if not relations:
            return 0

        # Convert Relation objects to dicts for insert
        values = [
            {
                "project_id": r.project_id if r.project_id else self.project_id,
                "from_id": r.from_id,
                "to_id": r.to_id,
                "to_name": r.to_name,
                "relation_type": r.relation_type,
                "context": r.context,
            }
            for r in relations
        ]

        async with db.scoped_session(self.session_maker) as session:
            # Check dialect to use appropriate insert
            dialect_name = session.bind.dialect.name if session.bind else "sqlite"

            if dialect_name == "postgresql":  # pragma: no cover
                # PostgreSQL: use RETURNING to count inserted rows
                # (rowcount is 0 for ON CONFLICT DO NOTHING)
                stmt = (  # pragma: no cover
                    pg_insert(Relation)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(Relation.id)
                )
                result = await session.execute(stmt)  # pragma: no cover
                return len(result.fetchall())  # pragma: no cover
            else:
                # SQLite: rowcount works correctly
                stmt = sqlite_insert(Relation).values(values)
                stmt = stmt.on_conflict_do_nothing()
                result = cast(CursorResult[Any], await session.execute(stmt))
                return result.rowcount if result.rowcount > 0 else 0

    def get_load_options(self) -> List[LoaderOption]:
        return [selectinload(Relation.from_entity), selectinload(Relation.to_entity)]
