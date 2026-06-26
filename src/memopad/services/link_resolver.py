"""Service for resolving markdown links to permalinks."""

import asyncio
from typing import Optional, Tuple


from loguru import logger

from memopad.models import Entity
from memopad.repository.entity_alias_repository import EntityAliasRepository
from memopad.repository.entity_repository import EntityRepository
from memopad.schemas.search import SearchQuery, SearchItemType
from memopad.services.search_service import SearchService


class LinkResolver:
    """Service for resolving markdown links to permalinks.

    Uses a combination of exact matching and search-based resolution:
    1. Try exact permalink match (fastest)
    2. Try exact title match
    3. Try explicit frontmatter alias match (MemoPad-native entity alias layer)
    4. Try exact file path match
    5. Try file path with .md extension (for folder/title patterns)
    6. Fall back to search for fuzzy matching when strict mode is disabled

    Alias resolution is intentionally exact and conservative. MemoPad stores user-authored
    frontmatter aliases but does not invent aliases or merge entities automatically.
    """

    def __init__(
        self,
        entity_repository: EntityRepository,
        search_service: SearchService,
        alias_repository: EntityAliasRepository | None = None,
    ):
        """Initialize with repositories."""
        self.entity_repository = entity_repository
        self.search_service = search_service
        self.alias_repository = alias_repository

    async def resolve_link(
        self,
        link_text: str,
        use_search: bool = True,
        strict: bool = False,
        source_path: Optional[str] = None,
    ) -> Optional[Entity]:
        """Resolve a markdown link to a permalink.

        Args:
            link_text: The link text to resolve
            use_search: Whether to use search-based fuzzy matching as fallback
            strict: If True, only exact matches are allowed (no fuzzy search fallback)
            source_path: Optional path of the source file containing the link.
                        Used to prefer notes closer to the source (context-aware resolution).
        """
        logger.trace(f"Resolving link: {link_text} (source: {source_path})")

        # Clean link text and extract any alias
        clean_text, alias = self._normalize_link_text(link_text)

        # --- Path Resolution ---
        # Note: All paths in Basic Memory are stored as POSIX strings (forward slashes)
        # for cross-platform compatibility. See entity_repository.py which normalizes
        # paths using Path().as_posix(). This allows consistent path operations here.

        # --- Relative Path Resolution ---
        # Trigger: source_path is provided AND link contains "/"
        # Why: Resolve paths like [[nested/deep-note]] relative to source folder first
        # Outcome: [[nested/deep-note]] from testing/link-test.md → testing/nested/deep-note.md
        if source_path and "/" in clean_text:
            source_folder = source_path.rsplit("/", 1)[0] if "/" in source_path else ""
            if source_folder:
                # Construct relative path from source folder
                relative_path = f"{source_folder}/{clean_text}"

                if relative_path.endswith(".md"):
                    # Already has .md — single lookup, no parallel pair possible.
                    entity = await self.entity_repository.get_by_file_path(relative_path)
                    if entity:
                        return entity
                else:
                    # The .md-prefixed path has priority (original tried it
                    # first and returned early), so resolve both concurrently
                    # and prefer the .md match on a tie.
                    md_entity, as_is_entity = await asyncio.gather(
                        self.entity_repository.get_by_file_path(f"{relative_path}.md"),
                        self.entity_repository.get_by_file_path(relative_path),
                    )
                    if md_entity:
                        return md_entity
                    if as_is_entity:
                        return as_is_entity

        # When source_path is provided, use context-aware resolution:
        # Check both permalink and title matches, prefer closest to source.
        # Example: [[testing]] from folder/note.md prefers folder/testing.md
        # over a root testing.md with permalink "testing".
        if source_path:
            # Permalink and title lookups are independent — run them concurrently
            # instead of sequentially. Precedence is preserved: the permalink match
            # is appended to `candidates` first, title matches after (deduped).
            candidates: list[Entity] = []
            permalink_entity, title_entities = await asyncio.gather(
                self.entity_repository.get_by_permalink(clean_text),
                self.entity_repository.get_by_title(clean_text),
            )

            if permalink_entity:
                candidates.append(permalink_entity)

            for entity in title_entities:
                # Avoid duplicates (permalink match might also be in title matches)
                if entity.id not in [c.id for c in candidates]:
                    candidates.append(entity)

            if candidates:
                if len(candidates) == 1:
                    return candidates[0]
                else:
                    # Multiple candidates - pick closest to source
                    return self._find_closest_entity(candidates, source_path)

        # Standard resolution (no source context): permalink first, then title
        # 1. Try exact permalink match first (most efficient)
        entity = await self.entity_repository.get_by_permalink(clean_text)
        if entity:
            logger.debug(f"Found exact permalink match: {entity.permalink}")
            return entity

        # 2. Try exact title match
        found = await self.entity_repository.get_by_title(clean_text)
        if found:
            # Return first match (shortest path) if no source context
            entity = found[0]
            logger.debug(f"Found title match: {entity.title}")
            return entity

        # 2.5 Try explicit frontmatter alias match before path fallback.
        # Trigger: permalink/title resolution failed and alias repository is wired.
        # Why: support user-authored aliases such as [[Newton]] -> Isaac Newton without fuzzy guessing.
        # Outcome: exact alias matches resolve before path and search fallbacks.
        if self.alias_repository:
            alias_match = await self.alias_repository.find_by_alias(clean_text)
            if alias_match:
                entity = await self.entity_repository.get_by_id(alias_match.entity_id)
                if entity:
                    logger.debug(f"Found alias match: {entity.title} via {clean_text}")
                    return entity

        # 3. Try file path
        found_path = await self.entity_repository.get_by_file_path(clean_text)
        if found_path:
            logger.debug(f"Found entity with path: {found_path.file_path}")
            return found_path

        # 4. Try file path with .md extension if not already present
        if not clean_text.endswith(".md") and "/" in clean_text:
            file_path_with_md = f"{clean_text}.md"
            found_path_md = await self.entity_repository.get_by_file_path(file_path_with_md)
            if found_path_md:
                logger.debug(f"Found entity with path (with .md): {found_path_md.file_path}")
                return found_path_md

        # In strict mode, don't try fuzzy search - return None if no exact match found
        if strict:
            return None

        # 5. Fall back to search for fuzzy matching (only if not in strict mode)
        if use_search and "*" not in clean_text:
            results = await self.search_service.search(
                query=SearchQuery(text=clean_text, entity_types=[SearchItemType.ENTITY]),
            )

            if results:
                # The search repository already returns results best-first regardless of
                # backend (SQLite bm25() sorts ASC — lower=better; Postgres ts_rank sorts
                # DESC — higher=better). Re-sorting by `score` here would be correct for one
                # backend and wrong for the other, so trust the repository's ordering and
                # take the first result.
                best_match = results[0]
                logger.trace(
                    f"Selected best match from {len(results)} results: {best_match.permalink}"
                )
                if best_match.permalink:
                    return await self.entity_repository.get_by_permalink(best_match.permalink)

        # if we couldn't find anything then return None
        return None

    def _normalize_link_text(self, link_text: str) -> Tuple[str, Optional[str]]:
        """Normalize link text and extract alias if present.

        Args:
            link_text: Raw link text from markdown

        Returns:
            Tuple of (normalized_text, alias or None)
        """
        # Strip whitespace
        text = link_text.strip()

        # Remove enclosing brackets if present
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2]

        # Handle wiki link aliases (format: [[actual|alias]])
        alias = None
        if "|" in text:
            text, alias = text.split("|", 1)
            text = text.strip()
            alias = alias.strip()
        else:
            # Strip whitespace from text even if no alias
            text = text.strip()

        return text, alias

    def _find_closest_entity(self, entities: list[Entity], source_path: str) -> Entity:
        """Find the entity closest to the source file path.

        Context-aware resolution: prefer notes in the same folder or closer in hierarchy.

        Proximity Scoring Algorithm:
        - Priority 0: Same folder as source (best match)
        - Priority 1-N: Ancestor folders (N = levels up from source)
        - Priority 100+N: Descendant folders (N = levels down, deprioritized)
        - Priority 1000: Completely unrelated paths (least preferred)
        - Ties are broken by shortest absolute path (consistent behavior)

        Args:
            entities: List of entities with the same title
            source_path: Path of the file containing the link

        Returns:
            The entity closest to the source path
        """
        # Extract source folder (everything before the last /)
        source_folder = source_path.rsplit("/", 1)[0] if "/" in source_path else ""

        def path_proximity(entity: Entity) -> Tuple[int, int]:
            """Return (proximity_score, path_length) for sorting.

            Lower is better for both values.
            """
            entity_path = entity.file_path
            entity_folder = entity_path.rsplit("/", 1)[0] if "/" in entity_path else ""

            # Trigger: entity is in the same folder as source
            # Why: same-folder notes are most contextually relevant
            # Outcome: priority = 0 (best), ties broken by shortest path
            if entity_folder == source_folder:
                return (0, len(entity_path))

            # Trigger: entity is in an ancestor folder of source
            # e.g., source is "a/b/c/file.md", entity is "a/b/note.md" -> ancestor
            # Why: ancestors are contextually relevant (shared parent context)
            # Outcome: priority = levels_up (1, 2, 3...), closer ancestors preferred
            if source_folder.startswith(entity_folder + "/") if entity_folder else source_folder:
                # Count how many levels up
                if entity_folder:
                    levels_up = source_folder.count("/") - entity_folder.count("/")
                else:
                    # Root level
                    levels_up = source_folder.count("/") + 1
                return (levels_up, len(entity_path))

            # Trigger: entity is in a descendant folder of source
            # e.g., source is "a/file.md", entity is "a/b/c/note.md" -> descendant
            # Why: descendants are less contextually relevant than ancestors
            # Outcome: priority = 100 + levels_down, significantly deprioritized
            if entity_folder.startswith(source_folder + "/") if source_folder else entity_folder:
                if source_folder:
                    levels_down = entity_folder.count("/") - source_folder.count("/")
                else:
                    # Source is at root
                    levels_down = entity_folder.count("/") + 1
                return (100 + levels_down, len(entity_path))

            # Trigger: entity is in a completely unrelated path
            # Why: no folder relationship means minimal contextual relevance
            # Outcome: priority = 1000, only selected if no related paths exist
            return (1000, len(entity_path))

        # Sort by proximity (lower is better), then by path length (shorter is better)
        return min(entities, key=path_proximity)
