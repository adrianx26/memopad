"""Repository for ObservationSchema — the per-project category registry."""

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import update

from memopad.models.observation_schema import ObservationSchema
from memopad.repository.repository import Repository


class ObservationSchemaRepository(Repository[ObservationSchema]):
    """Repository for ObservationSchema model.

    All queries are scoped to project_id (inherited from base Repository).
    This repository supports MemoPad's Schema Layer for observation categories.
    """

    def __init__(self, session_maker, project_id: int):
        super().__init__(session_maker, ObservationSchema, project_id=project_id)

    async def find_by_project(self) -> Sequence[ObservationSchema]:
        """Return all schemas for the current project, ordered by frequency descending."""
        query = (
            self.select()
            .order_by(ObservationSchema.frequency.desc())
        )
        result = await self.execute_query(query, use_query_options=False)
        return result.scalars().all()

    async def find_by_name(self, name: str) -> Optional[ObservationSchema]:
        """Find a schema by its canonical name (exact, case-sensitive match)."""
        query = self.select().where(
            ObservationSchema.name == name,
        )
        result = await self.execute_query(query, use_query_options=False)
        return result.scalars().one_or_none()

    async def find_by_name_or_alias(self, raw_category: str) -> Optional[ObservationSchema]:
        """Find a schema matching raw_category as canonical name or alias.

        Tries lowercase canonical name first, then scans alias lists.
        Returns None when the category is completely unknown.
        """
        lower = raw_category.lower()

        # 1. Try exact canonical name (normalised to lowercase)
        schema = await self.find_by_name(lower)
        if schema:
            return schema

        # 2. Scan all schemas for this project checking alias lists
        all_schemas = await self.find_by_project()
        for schema in all_schemas:
            aliases_lower = [a.lower() for a in (schema.aliases or [])]
            if lower in aliases_lower or raw_category in (schema.aliases or []):
                return schema

        return None

    async def upsert_schema(self, name: str) -> ObservationSchema:
        """Create a new schema entry or increment frequency for an existing one.

        Args:
            name: Canonical category name (will be lowercased).

        Returns:
            The created or updated ObservationSchema.
        """
        lower_name = name.lower()
        existing = await self.find_by_name(lower_name)

        if existing:
            # Increment frequency
            from memopad import db
            async with db.scoped_session(self.session_maker) as session:
                await session.execute(
                    update(ObservationSchema)
                    .where(ObservationSchema.id == existing.id)
                    .values(
                        frequency=existing.frequency + 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            return await self.find_by_id(existing.id)  # type: ignore[return-value]

        now = datetime.now(timezone.utc)
        return await self.create({
            "project_id": self.project_id,
            "name": lower_name,
            "aliases": [],
            "frequency": 1,
            "created_at": now,
            "updated_at": now,
        })

    async def add_alias(self, schema_id: int, alias: str) -> None:
        """Register an alias for an existing schema.

        Idempotent: adding an alias that already exists is a no-op.
        """
        schema = await self.find_by_id(schema_id)
        if not schema:
            return  # pragma: no cover

        current_aliases: list = list(schema.aliases or [])
        if alias in current_aliases:
            return  # already registered

        current_aliases.append(alias)

        from memopad import db
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                update(ObservationSchema)
                .where(ObservationSchema.id == schema_id)
                .values(
                    aliases=current_aliases,
                    updated_at=datetime.now(timezone.utc),
                )
            )
