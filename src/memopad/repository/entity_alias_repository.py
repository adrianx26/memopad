"""Repository for EntityAlias."""

from typing import Optional, Sequence

from sqlalchemy import select, asc

from memopad.models.entity_alias import EntityAlias
from memopad.repository.repository import Repository


class EntityAliasRepository(Repository[EntityAlias]):
    """Repository for entity aliases.

    Aliases are explicit frontmatter-derived mappings used by LinkResolver to
    improve WikiLink resolution without changing entity identity or merging files.
    """

    def __init__(self, session_maker, project_id: int):
        super().__init__(session_maker, EntityAlias, project_id=project_id)

    async def find_by_alias(self, alias: str) -> Optional[EntityAlias]:
        """Find an alias in the current project."""
        query = select(EntityAlias).where(EntityAlias.alias == alias)
        result = await self.execute_query(query)
        return result.scalars().one_or_none()

    async def find_by_entity(self, entity_id: int) -> Sequence[EntityAlias]:
        """Find all aliases for an entity."""
        query = select(EntityAlias).where(EntityAlias.entity_id == entity_id).order_by(
            asc(EntityAlias.id)
        )
        result = await self.execute_query(query)
        return result.scalars().all()

    async def upsert_aliases(
        self,
        entity_id: int,
        aliases: Sequence[str],
        source: str = "frontmatter",
    ) -> None:
        """Replace aliases for an entity with the supplied set."""
        from memopad import db

        async with db.scoped_session(self.session_maker) as session:
            existing = await session.execute(
                select(EntityAlias).where(EntityAlias.entity_id == entity_id)
            )
            for alias_row in existing.scalars().all():
                await session.delete(alias_row)

            seen: set[str] = set()
            for raw_alias in aliases:
                alias = str(raw_alias).strip()
                if not alias or alias in seen:
                    continue
                seen.add(alias)

                existing_alias = (
                    await session.execute(
                        select(EntityAlias).where(
                            EntityAlias.alias == alias,
                            EntityAlias.project_id == self.project_id,
                        )
                    )
                ).scalars().one_or_none()
                if existing_alias and existing_alias.entity_id != entity_id:
                    continue

                session.add(
                    EntityAlias(
                        entity_id=entity_id,
                        project_id=self.project_id,
                        alias=alias,
                        source=source,
                    )
                )

    async def delete_by_entity(self, entity_id: int) -> None:
        """Delete aliases for an entity."""
        from memopad import db
        from sqlalchemy import delete

        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                delete(EntityAlias).where(EntityAlias.entity_id == entity_id)
            )
