"""Repository for managing Observation objects."""

from typing import Dict, List, Sequence


from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db
from memopad.models import Observation
from memopad.repository.repository import Repository


class ObservationRepository(Repository[Observation]):
    """Repository for Observation model with memory-specific operations."""

    def __init__(self, session_maker: async_sessionmaker, project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by
        """
        super().__init__(session_maker, Observation, project_id=project_id)

    async def find_by_entity(self, entity_id: int) -> Sequence[Observation]:
        """Find all observations for a specific entity."""
        query = select(Observation).filter(Observation.entity_id == entity_id)
        result = await self.execute_query(query)
        return result.scalars().all()

    async def replace_observations(
        self, entity_id: int, observations: List[Observation]
    ) -> Sequence[Observation]:
        """Atomically replace an entity's observations in a single transaction.

        Deletes the entity's existing observations and inserts the new ones in
        one session/commit, so a failure mid-replace can't leave the entity with
        zero observations (the old delete-then-add-on-separate-sessions path
        committed the delete before the add, losing all observations if the add
        then raised).
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                delete(Observation).where(Observation.entity_id == entity_id)
            )
            for model in observations:
                self._set_project_id_if_needed(model)
            if observations:
                session.add_all(observations)
                await session.flush()
                result = await session.execute(
                    select(Observation)
                    .where(Observation.id.in_([m.id for m in observations]))
                    .options(*self.get_load_options())
                )
                return result.scalars().all()
            return []

    async def find_by_context(self, context: str) -> Sequence[Observation]:
        """Find observations with a specific context."""
        query = self._add_project_filter(
            select(Observation).filter(Observation.context == context)
        )
        result = await self.execute_query(query)
        return result.scalars().all()

    async def find_by_category(self, category: str) -> Sequence[Observation]:
        """Find observations with a specific context."""
        query = self._add_project_filter(
            select(Observation).filter(Observation.category == category)
        )
        result = await self.execute_query(query)
        return result.scalars().all()

    async def observation_categories(self) -> Sequence[str]:
        """Return a list of all observation categories."""
        query = self._add_project_filter(select(Observation.category).distinct())
        result = await self.execute_query(query, use_query_options=False)
        return result.scalars().all()

    async def find_by_entities(self, entity_ids: List[int]) -> Dict[int, List[Observation]]:
        """Find all observations for multiple entities in a single query.

        Args:
            entity_ids: List of entity IDs to fetch observations for

        Returns:
            Dictionary mapping entity_id to list of observations
        """
        if not entity_ids:  # pragma: no cover
            return {}

        # Query observations for all entities in the list
        query = select(Observation).filter(Observation.entity_id.in_(entity_ids))
        result = await self.execute_query(query)
        observations = result.scalars().all()

        # Group observations by entity_id
        observations_by_entity = {}
        for obs in observations:
            if obs.entity_id not in observations_by_entity:
                observations_by_entity[obs.entity_id] = []
            observations_by_entity[obs.entity_id].append(obs)

        return observations_by_entity
