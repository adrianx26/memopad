"""SchemaService — normalises observation categories against the per-project registry.

Implements MemGraphRAG's Ontology Layer noise gate in a lightweight, rule-based way:

- When an LLM writes `- [Status] active`, SchemaService maps "Status" → "status"
  if "status" is the registered canonical name (or if "Status" is a known alias).
- If the category is completely unknown it is registered automatically with
  frequency=1 so the LLM can later see it is rare and decide to prune it.
- SchemaService never blocks writes; it only normalises and tracks.
- SchemaService does not rewrite markdown. The source file remains authoritative;
  normalization affects the derived observation index used by retrieval and context tools.
"""

from dataclasses import dataclass
from typing import Sequence

from loguru import logger

from memopad.models.observation_schema import ObservationSchema
from memopad.repository.observation_schema_repository import ObservationSchemaRepository


@dataclass
class ConsolidationSuggestion:
    """A low-frequency schema that may be a noise entry or a duplicate."""

    schema_id: int
    name: str
    frequency: int
    possible_duplicate_of: str | None  # name of a high-freq schema it resembles


class SchemaService:
    """Normalises observation categories and maintains the per-project schema registry."""

    def __init__(self, schema_repository: ObservationSchemaRepository) -> None:
        self.schema_repository = schema_repository

    async def normalize_category(self, raw_category: str) -> str:
        """Map a raw category label to its canonical name.

        Steps:
        1. Look up raw_category (case-insensitive) in the registry.
        2. If found as canonical name or alias → return canonical name and
           increment frequency.
        3. If not found → register as a new canonical name (lowercased),
           frequency=1; also register the original casing as an alias if it
           differs from the lower-cased name.

        Args:
            raw_category: The category string written by the LLM, e.g. "Status".

        Returns:
            Canonical category name, e.g. "status".
        """
        schema = await self.schema_repository.find_by_name_or_alias(raw_category)

        if schema:
            # Increment frequency to reflect usage
            await self.schema_repository.upsert_schema(schema.name)
            logger.debug(
                f"Category '{raw_category}' normalised to '{schema.name}' "
                f"(freq now {schema.frequency + 1})"
            )
            # Register the original casing as an alias if it's different
            lower = raw_category.lower()
            if raw_category != lower and raw_category not in (schema.aliases or []):
                await self.schema_repository.add_alias(schema.id, raw_category)
            return schema.name

        # Unknown category — register it
        lower = raw_category.lower()
        new_schema = await self.schema_repository.upsert_schema(lower)

        # If the raw casing differs, record it as an alias
        if raw_category != lower:
            await self.schema_repository.add_alias(new_schema.id, raw_category)

        logger.debug(
            f"New category registered: '{lower}' "
            f"(original: '{raw_category}', freq=1)"
        )
        return lower

    async def get_schemas(self) -> Sequence[ObservationSchema]:
        """Return all schemas for the current project, ordered by frequency descending."""
        return await self.schema_repository.find_by_project()

    async def suggest_consolidation(self) -> list[ConsolidationSuggestion]:
        """Identify rare (frequency=1) schemas that are likely noise or duplicates.

        For each rare schema, checks if any high-frequency schema has a similar
        name (using simple character overlap) and suggests consolidation.

        Returns:
            List of ConsolidationSuggestion for rare categories.
        """
        all_schemas = await self.schema_repository.find_by_project()
        stable = [s for s in all_schemas if s.frequency > 1]
        rare = [s for s in all_schemas if s.frequency == 1]

        suggestions: list[ConsolidationSuggestion] = []
        for rare_schema in rare:
            best_match: str | None = None
            best_score = 0.0
            for stable_schema in stable:
                score = _name_overlap(rare_schema.name, stable_schema.name)
                if score > best_score:
                    best_score = score
                    best_match = stable_schema.name

            suggestions.append(
                ConsolidationSuggestion(
                    schema_id=rare_schema.id,
                    name=rare_schema.name,
                    frequency=rare_schema.frequency,
                    possible_duplicate_of=best_match if best_score > 0.5 else None,
                )
            )

        return suggestions


def _name_overlap(a: str, b: str) -> float:
    """Compute character-overlap similarity for two short strings."""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    common = sum((ca & cb).values())
    total = sum(ca.values()) + sum(cb.values())
    if total == 0:
        return 1.0
    return (2 * common) / total
