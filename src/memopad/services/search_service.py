"""Service for search operations."""

import ast
import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from typing import AbstractSet, List, Optional, Dict, Any


from dateparser import parse
from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy import bindparam, text

from memopad import db
from memopad.models import Entity
from memopad.repository import EntityRepository
from memopad.repository.search_repository import SearchRepository, SearchIndexRow
from memopad.schemas.search import SearchQuery, SearchItemType
from memopad.services import FileService
from memopad.services.embedding_service import (
    BACKFILL_BATCH_DEFAULT,
    ITEM_TYPE_ENTITY,
    ITEM_TYPE_OBSERVATION,
    ITEM_TYPE_RELATION,
    EmbeddingService,
)

# Maximum size for content_stems field to stay under Postgres's 8KB index row limit.
# We use 6000 characters to leave headroom for other indexed columns and overhead.
MAX_CONTENT_STEMS_SIZE = 6000

# Incremental reindex schema version. Bump this whenever the FTS schema, the
# FTS5 tokenizer config, or _generate_variants/index_entity_markdown logic
# changes the indexed output for an unchanged entity. Rows in reindex_state
# carrying an older version are treated as stale and force a reindex, so a bump
# is the lever for triggering a clean rebuild after such a change without
# dropping the index.
REINDEX_INDEX_VERSION = 1


def _mtime_to_datetime(entity: Entity) -> datetime:
    """Convert entity mtime (file modification time) to datetime.

    Returns the file's actual modification time, falling back to updated_at
    if mtime is not available.
    """
    if entity.mtime:
        return datetime.fromtimestamp(entity.mtime).astimezone()
    return entity.updated_at


class SearchService:
    """Service for search operations.

    Supports three primary search modes:
    1. Exact permalink lookup
    2. Pattern matching with * (e.g., 'specs/*')
    3. Full-text search across title/content
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
        file_service: FileService,
        session_maker=None,
        project_id: Optional[int] = None,
    ):
        self.repository = search_repository
        self.entity_repository = entity_repository
        self.file_service = file_service
        # Embedding wiring (optional). When session_maker + project_id are injected
        # (the API/v2 DI path), every indexed note also writes its semantic vector.
        # When absent (legacy 3-arg construction), embeddings stay disabled and
        # behavior is unchanged. See Phase 3 of optimax.md.
        self.session_maker = session_maker
        self.project_id = project_id
        self._embedding_service = None  # lazy, cached on first use
        # Buffered embedding writer (Fix 4). When batch mode is active, per-entity
        # embedding items accumulate here and flush in BACKFILL_BATCH_DEFAULT-sized
        # chunks, so a sync sweep that touches N files makes ceil(total_items /
        # batch_size) model calls instead of one per file. Outside batch mode
        # (single-file updates, API writes) each upsert embeds immediately as
        # before. asyncio is single-threaded, so the list needs no locking even
        # though sync_file coroutines feed it concurrently during a parallel scan.
        self._embedding_batch_mode = False
        self._embedding_buffer: list[tuple[str, int, str]] = []

    async def _get_embedding_service(self):
        """Return a cached EmbeddingService, or None when embeddings are off.

        The provider (ONNX model) is cached at module scope in embedding_service,
        so this only does real work once per process; subsequent calls are a
        cached attribute read.
        """
        if self._embedding_service is not None:
            return self._embedding_service
        if self.session_maker is None or self.project_id is None:
            return None
        from memopad.services.embedding_service import EmbeddingService

        self._embedding_service = EmbeddingService.maybe_create(self.session_maker, self.project_id)
        return self._embedding_service

    @staticmethod
    def _entity_embedding_text(entity: Entity, content: Optional[str]) -> str:
        """Build the text we embed for an entity: title + permalink + content.

        Permalink is included so path-based queries (e.g. "specs/search") match
        semantically even when the body lacks the literal terms.
        """
        parts = [entity.title or "", entity.permalink or ""]
        if content:
            parts.append(content)
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _observation_embedding_text(obs) -> str:
        """Build the text we embed for an observation (a single fact): category + content."""
        parts = [obs.category or "", obs.content or ""]
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _relation_embedding_text(rel) -> str:
        """Build the text we embed for an outgoing relation: from → type → to.

        Captures the relationship as a phrase so queries like "what depends on
        the parser" match the relation itself, not just the endpoints.
        """
        from_title = rel.from_entity.title if rel.from_entity else ""
        if rel.to_entity:
            to_title = rel.to_entity.title
            return f"{from_title} → {rel.relation_type} → {to_title}"
        return f"{from_title} → {rel.relation_type}"

    def _entity_embedding_items(
        self, entity: Entity, content: Optional[str]
    ) -> list[tuple[str, int, str]]:
        """Build the (item_type, item_id, text) batch for an entity and its facts/relations.

        One entity yields one entity vector plus a vector per observation and per
        outgoing relation, so semantic search can surface a specific fact or edge.
        """
        items: list[tuple[str, int, str]] = [
            (ITEM_TYPE_ENTITY, entity.id, self._entity_embedding_text(entity, content))
        ]
        for obs in entity.observations:
            items.append((ITEM_TYPE_OBSERVATION, obs.id, self._observation_embedding_text(obs)))
        for rel in entity.outgoing_relations:
            items.append((ITEM_TYPE_RELATION, rel.id, self._relation_embedding_text(rel)))
        return items

    def _entity_embedding_keys(self, entity: Entity) -> list[tuple[str, int]]:
        """The (item_type, item_id) keys for an entity's vectors (used on delete)."""
        keys: list[tuple[str, int]] = [(ITEM_TYPE_ENTITY, entity.id)]
        keys.extend((ITEM_TYPE_OBSERVATION, o.id) for o in entity.observations)
        keys.extend((ITEM_TYPE_RELATION, r.id) for r in entity.outgoing_relations)
        return keys

    async def _upsert_entity_embeddings(self, entity: Entity, content: Optional[str]) -> None:
        """Best-effort: write the entity's + its facts'/relations' vectors alongside FTS.

        Embeddings are optional; when disabled this is a no-op. A failure here
        must NOT break FTS indexing — we log and move on so a model/IO hiccup
        can't corrupt the keyword index that everything else depends on.

        In batch mode (Fix 4) the items are appended to ``_embedding_buffer`` and
        flushed in ``BACKFILL_BATCH_DEFAULT`` chunks instead of embedding
        immediately — this batches model calls across many files during a sync
        sweep. Outside batch mode each call embeds immediately.
        """
        try:
            svc = await self._get_embedding_service()
        except Exception as e:  # pragma: no cover
            logger.warning(f"Embedding service unavailable for entity_id={entity.id}: {e}")
            return
        if svc is None:
            return
        try:
            if content is None and entity.is_markdown:
                content = await self.file_service.read_entity_content(entity)
            items = self._entity_embedding_items(entity, content)
            if self._embedding_batch_mode:
                self._embedding_buffer.extend(items)
                if len(self._embedding_buffer) >= BACKFILL_BATCH_DEFAULT:
                    await self._flush_embedding_buffer(svc)
            else:
                await svc.upsert_batch(items)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Embedding upsert failed for entity_id={entity.id}: {e}")

    def begin_embedding_batch(self) -> None:
        """Activate buffered embedding writes for the duration of a bulk operation.

        Call ``flush_embedding_buffer`` when the bulk work is done so the
        remainder is written. Idempotent: safe to call when already batching.
        """
        self._embedding_batch_mode = True

    async def flush_embedding_buffer(self) -> None:
        """Flush any buffered embedding items and leave batch mode.

        Writes the remaining buffer in one ``upsert_batch`` call (which itself
        skips unchanged items via content-hash dedup) and turns batch mode off
        so subsequent single-entity writes embed immediately again.
        """
        if not self._embedding_batch_mode and not self._embedding_buffer:
            return
        self._embedding_batch_mode = False
        if not self._embedding_buffer:
            return
        try:
            svc = await self._get_embedding_service()
        except Exception as e:  # pragma: no cover
            logger.warning(f"Embedding service unavailable during flush: {e}")
            self._embedding_buffer.clear()
            return
        if svc is None:
            self._embedding_buffer.clear()
            return
        await self._flush_embedding_buffer(svc)

    async def _flush_embedding_buffer(self, svc: "EmbeddingService") -> None:
        """Drain ``_embedding_buffer`` into ``svc`` in batch-sized chunks."""
        if not self._embedding_buffer:
            return
        buffer = self._embedding_buffer
        self._embedding_buffer = []
        try:
            for i in range(0, len(buffer), BACKFILL_BATCH_DEFAULT):
                await svc.upsert_batch(buffer[i : i + BACKFILL_BATCH_DEFAULT])
        except Exception as e:  # pragma: no cover
            logger.warning(f"Embedding buffer flush failed ({len(buffer)} items): {e}")

    async def delete_entity_embeddings(self, entity: Entity) -> None:
        """Best-effort: drop an entity's vectors (entity + observations + relations).

        Public so the sync file-delete path can call the same cleanup the explicit
        API/CLI delete path uses, keeping the two from diverging.
        """
        try:
            svc = await self._get_embedding_service()
        except Exception:  # pragma: no cover
            return
        if svc is None:
            return
        try:
            await svc.delete_batch(self._entity_embedding_keys(entity))
        except Exception as e:  # pragma: no cover
            logger.warning(f"Embedding delete failed for entity_id={entity.id}: {e}")

    async def init_search_index(self):
        """Create FTS5 virtual table if it doesn't exist."""
        await self.repository.init_search_index()

    async def reindex_all(
        self,
        background_tasks: Optional[BackgroundTasks] = None,
        batch_size: int = BACKFILL_BATCH_DEFAULT,
        *,
        force: bool = False,
        incremental: bool = True,
    ) -> None:
        """Reindex content from the database.

        By default the reindex is **incremental**: entities whose indexed output
        is unchanged since the last reindex (per ``reindex_state``) are skipped
        entirely — no file read, no FTS write, no embedding model call — and only
        changed/new entities are re-indexed, with entries for vanished entities
        pruned. This avoids redoing the whole corpus on every call.

        Pass ``force=True`` (or ``incremental=False``) to run the legacy full
        wipe-and-rebuild: clear this project's FTS rows and vectors, then
        re-index and re-embed everything. The full path also repopulates
        ``reindex_state`` so the next incremental run benefits.

        The FTS index table is created if it does not yet exist, and cleared in
        place (DELETE, not DROP) so concurrent searches see stale-but-present
        results instead of an empty/missing table during a full rebuild.
        """
        if force or not incremental:
            await self._reindex_full(background_tasks, batch_size)
            return
        await self._reindex_incremental(background_tasks, batch_size)

    async def _reindex_full(
        self,
        background_tasks: Optional[BackgroundTasks] = None,
        batch_size: int = BACKFILL_BATCH_DEFAULT,
    ) -> None:
        """Full wipe-and-rebuild of this project's FTS index and embeddings.

        Clears this project's rows in place (DELETE) rather than DROP+recreate so the
        index table and its schema stay present throughout the rebuild. A concurrent
        search therefore sees stale-but-present results instead of an empty or
        missing table. The table is created if it does not yet exist.

        When embeddings are enabled, the project's vectors are cleared too (so notes
        deleted since the last reindex stop matching) and re-populated in batched
        chunks — one model call per chunk instead of one per note.

        ``reindex_state`` is wiped and repopulated for every current entity so a
        subsequent incremental reindex skips correctly instead of treating
        everything as new.
        """

        logger.info("Starting full reindex")
        # Ensure the index table exists (CREATE IF NOT EXISTS), then clear this
        # project's rows in place. We avoid DROP TABLE here so concurrent readers
        # never observe an empty/missing index during the (potentially long) rebuild.
        await self.init_search_index()
        await self.repository.execute_query(
            text("DELETE FROM search_index WHERE project_id = :project_id"),
            params={"project_id": self.repository.project_id},
        )

        # Trigger: embeddings are enabled for this project
        # Why: vectors for notes deleted since the last reindex would otherwise
        #      persist and match queries to content that no longer exists
        # Outcome: clear the project's vectors before re-embedding everything
        embedding_svc = await self._get_embedding_service()
        if embedding_svc is not None:
            await embedding_svc.clear_project(self.repository.project_id)

        # Reindex all entities into FTS, suppressing per-note embedding — the
        # batched backfill pass below embeds everything in far fewer model calls.
        # Load each note's content once so the FTS write and the backfill share it
        # instead of reading the file twice.
        logger.debug("Indexing entities")
        entities = await self.entity_repository.find_all()
        loaded: list[tuple[Entity, Optional[str]]] = []
        for entity in entities:
            content = (
                await self.file_service.read_entity_content(entity) if entity.is_markdown else None
            )
            await self.index_entity(
                entity, background_tasks, content=content, write_embeddings=False
            )
            loaded.append((entity, content))

        # Batched semantic backfill (only when embeddings are enabled).
        if embedding_svc is not None and loaded:
            await self._backfill_embeddings(loaded, batch_size)

        # Reset reindex_state to match the freshly rebuilt index so the next
        # incremental run skips unchanged entities.
        await self._ensure_reindex_state()
        await self.repository.execute_query(
            text("DELETE FROM reindex_state WHERE project_id = :project_id"),
            params={"project_id": self.repository.project_id},
        )
        await self._upsert_reindex_state(
            [
                (e.id, self._entity_fingerprint(e), REINDEX_INDEX_VERSION)
                for e in entities
            ]
        )

        logger.info("Reindex complete")

    async def _reindex_incremental(
        self,
        background_tasks: Optional[BackgroundTasks] = None,
        batch_size: int = BACKFILL_BATCH_DEFAULT,
    ) -> None:
        """Reindex only what changed since the last reindex.

        Loads per-entity fingerprints from ``reindex_state`` and compares them
        against the current entity rows (which sync has already refreshed).
        Unchanged entities are skipped; changed/new entities are re-indexed and
        re-embedded (in batched chunks); entities present in state but no longer
        in the DB have their FTS rows and embedding vectors pruned.
        """
        logger.info("Starting incremental reindex")
        await self.init_search_index()
        await self._ensure_reindex_state()
        state = await self._load_reindex_state()

        # find_all eager-loads observations + relations, so fingerprinting and
        # _entity_embedding_keys add no extra IO beyond this single query.
        entities = await self.entity_repository.find_all()
        current_ids = {e.id for e in entities}

        keep_keys: set[tuple[str, int]] = set()
        changed: list[Entity] = []
        skipped = 0
        for entity in entities:
            # Recompute the valid vector keys from current entities every run so
            # prune_project also catches id drift on entities we skip below.
            keep_keys.update(self._entity_embedding_keys(entity))
            fingerprint = self._entity_fingerprint(entity)
            prev = state.get(entity.id)
            if (
                prev is not None
                and prev[0] == fingerprint
                and prev[1] == REINDEX_INDEX_VERSION
            ):
                skipped += 1
                continue
            changed.append(entity)

        deleted_ids = [eid for eid in state if eid not in current_ids]

        # Re-index changed/new entities into FTS. index_entity_data already
        # deletes the entity's prior FTS rows (delete_by_entity_id) before
        # re-inserting, so no separate delete is needed. Embeddings are
        # suppressed here and written in one batched pass below.
        loaded: list[tuple[Entity, Optional[str]]] = []
        state_upserts: list[tuple[int, str, int]] = []
        for entity in changed:
            content = (
                await self.file_service.read_entity_content(entity)
                if entity.is_markdown
                else None
            )
            await self.index_entity_data(entity, content, write_embeddings=False)
            loaded.append((entity, content))
            state_upserts.append(
                (entity.id, self._entity_fingerprint(entity), REINDEX_INDEX_VERSION)
            )

        # Batched semantic embed of just the changed subset — one model call per
        # chunk instead of one per note. No-op when embeddings are disabled.
        embedding_svc = await self._get_embedding_service()
        if embedding_svc is not None and loaded:
            await self._backfill_embeddings(loaded, batch_size)

        # Prune FTS rows for entities that no longer exist. Observation and
        # relation rows carry their parent entity's entity_id, so this removes
        # all of a vanished entity's FTS rows.
        for eid in deleted_ids:
            await self.repository.delete_by_entity_id(eid)

        # Prune orphan embedding vectors (replaces the guarantee clear_project
        # gave in the full path). No-op when embeddings are disabled.
        if embedding_svc is not None:
            await embedding_svc.prune_project(self.repository.project_id, keep_keys)

        # Persist state: record reindexed entities, drop vanished ones.
        await self._upsert_reindex_state(state_upserts)
        await self._delete_reindex_state(deleted_ids)

        logger.info(
            f"Incremental reindex complete: {len(changed)} reindexed, "
            f"{skipped} skipped, {len(deleted_ids)} pruned"
        )

    # --- reindex_state bookkeeping ---

    async def _ensure_reindex_state(self) -> None:
        """Create the reindex_state table if it doesn't exist.

        Mirrors EmbeddingService's lazy table creation so databases that skip
        Alembic (e.g. the test fixture, which uses Base.metadata.create_all)
        still work. The DDL matches the o9c0d1e2f3a4 migration.
        """
        await self.repository.execute_query(
            text(
                "CREATE TABLE IF NOT EXISTS reindex_state ("
                " project_id INTEGER NOT NULL,"
                " entity_id INTEGER NOT NULL,"
                " fingerprint VARCHAR NOT NULL,"
                " index_version INTEGER NOT NULL,"
                " indexed_at TIMESTAMP WITH TIME ZONE,"
                " PRIMARY KEY (project_id, entity_id)"
                ")"
            ),
            params={},
        )

    @staticmethod
    def _entity_fingerprint(entity: Entity) -> str:
        """A stable hash of everything that determines an entity's indexed output.

        ``checksum`` is the SHA-256 of the file content, so it covers the derived
        observation/relation text as well as the note body. The entity-level
        fields catch metadata/permalink-only edits that don't change the file.
        The sorted observation/relation id lists catch id drift (e.g. an
        observation deleted and recreated with a new id from unchanged file
        content) that would otherwise leave stale FTS rows. ``mtime``/``size``
        are the fallback signal for non-markdown entities where ``checksum`` may
        be None.
        """
        payload = {
            "checksum": entity.checksum,
            "title": entity.title,
            "permalink": entity.permalink,
            "entity_metadata": entity.entity_metadata,
            "entity_type": entity.entity_type,
            "content_type": entity.content_type,
            "file_path": entity.file_path,
            "mtime": entity.mtime,
            "size": entity.size,
            "obs_ids": sorted(o.id for o in entity.observations),
            "outgoing_rel_ids": sorted(r.id for r in entity.outgoing_relations),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _now_sql(self) -> str:
        """Dialect-aware 'current timestamp' SQL fragment for reindex_state writes."""
        # The search repository's session bind determines the backend. We can't
        # inspect the dialect through the protocol directly, so key off the
        # repository class name's backend hint as a lightweight, stable check.
        backend = type(self.repository).__name__.lower()
        return "now()" if "postgres" in backend else "datetime('now')"

    async def _load_reindex_state(self) -> Dict[int, tuple[str, int]]:
        """Return ``{entity_id: (fingerprint, index_version)}`` for this project."""
        result = await self.repository.execute_query(
            text(
                "SELECT entity_id, fingerprint, index_version "
                "FROM reindex_state WHERE project_id = :pid"
            ),
            params={"pid": self.repository.project_id},
        )
        return {row[0]: (row[1], row[2]) for row in result.fetchall()}

    async def _upsert_reindex_state(
        self, rows: List[tuple[int, str, int]]
    ) -> None:
        """Insert or update reindex_state rows (entity_id, fingerprint, version)."""
        if not rows:
            return
        now_sql = self._now_sql()
        await self.repository.execute_query(
            text(
                "INSERT INTO reindex_state "
                "(project_id, entity_id, fingerprint, index_version, indexed_at) "
                f"VALUES (:pid, :eid, :fp, :iv, {now_sql}) "
                "ON CONFLICT(project_id, entity_id) DO UPDATE SET "
                " fingerprint = excluded.fingerprint, "
                " index_version = excluded.index_version, "
                " indexed_at = excluded.indexed_at"
            ),
            params=[
                {
                    "pid": self.repository.project_id,
                    "eid": r[0],
                    "fp": r[1],
                    "iv": r[2],
                }
                for r in rows
            ],
        )

    async def _delete_reindex_state(self, entity_ids: List[int]) -> None:
        """Drop reindex_state rows for the given entity ids."""
        if not entity_ids:
            return
        await self.repository.execute_query(
            text(
                "DELETE FROM reindex_state WHERE project_id = :pid AND entity_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            params={
                "pid": self.repository.project_id,
                "ids": entity_ids,
            },
        )

    async def _backfill_embeddings(
        self,
        loaded: list[tuple[Entity, Optional[str]]],
        batch_size: int,
    ) -> None:
        """Embed every entity + observation + relation in batched chunks.

        Gathering across notes lets one model call cover `batch_size` items, so a
        full backfill costs ceil(total_items / batch_size) model calls instead of
        one per note. Best-effort: a failure logs and aborts the backfill but does
        not undo the FTS rebuild.
        """
        svc = await self._get_embedding_service()
        if svc is None:
            return
        items: list[tuple[str, int, str]] = []
        for entity, content in loaded:
            items.extend(self._entity_embedding_items(entity, content))
        if not items:
            return
        logger.info(f"Backfilling {len(items)} embedding items in chunks of {batch_size}")
        for i in range(0, len(items), batch_size):
            try:
                await svc.upsert_batch(items[i : i + batch_size])
            except Exception as e:  # pragma: no cover
                logger.warning(f"Embedding backfill chunk failed at offset {i}: {e}")
                raise

    async def search(self, query: SearchQuery, limit=10, offset=0) -> List[SearchIndexRow]:
        """Search across all indexed content.

        Supports three modes:
        1. Exact permalink: finds direct matches for a specific path
        2. Pattern match: handles * wildcards in paths
        3. Text search: full-text search across title/content
        """
        # Support tag:<tag> shorthand by mapping to tags filter
        if query.text:
            text = query.text.strip()
            if text.lower().startswith("tag:"):
                tag_values = re.split(r"[,\s]+", text[4:].strip())
                tags = [t for t in tag_values if t]
                if tags:
                    query.tags = tags
                    query.text = None

        if query.no_criteria():
            logger.debug("no criteria passed to query")
            return []

        logger.trace(f"Searching with query: {query}")

        after_date = (
            (
                query.after_date
                if isinstance(query.after_date, datetime)
                else parse(query.after_date)
            )
            if query.after_date
            else None
        )

        # Merge structured metadata filters (explicit + convenience fields)
        metadata_filters: Optional[Dict[str, Any]] = None
        if query.metadata_filters or query.tags or query.status:
            metadata_filters = dict(query.metadata_filters or {})
            if query.tags:
                metadata_filters.setdefault("tags", query.tags)
            if query.status:
                metadata_filters.setdefault("status", query.status)

        # search
        results = await self.repository.search(
            search_text=query.text,
            permalink=query.permalink,
            permalink_match=query.permalink_match,
            title=query.title,
            types=query.types,
            search_item_types=query.entity_types,
            after_date=after_date,
            metadata_filters=metadata_filters,
            limit=limit,
            offset=offset,
        )

        return results

    @staticmethod
    @lru_cache(maxsize=4096)
    def _generate_variants(text: str) -> AbstractSet[str]:
        """Generate text variants for better fuzzy matching.

        Creates variations of the text to improve match chances:
        - Original form
        - Lowercase form
        - Path segments (for permalinks)
        - Common word boundaries

        Memoized: this is a pure function of `text`, called once per observation
        and per relation during indexing. A note with many observations sharing
        categories/content re-computes the same variants repeatedly; the lru_cache
        turns those into O(1) lookups. Returns an immutable frozenset so callers
        can't accidentally corrupt the cached value.
        """
        variants = {text, text.lower()}

        # Add path segments
        if "/" in text:
            variants.update(p.strip() for p in text.split("/") if p.strip())

        # Add word boundaries
        variants.update(w.strip() for w in text.lower().split() if w.strip())

        # Trigrams disabled: They create massive search index bloat, increasing DB size significantly
        # and slowing down indexing performance. FTS5 search works well without them.
        # See: https://github.com/basicmachines-co/memopad/issues/351
        # variants.update(text[i : i + 3].lower() for i in range(len(text) - 2))

        return frozenset(variants)

    def _extract_entity_tags(self, entity: Entity) -> List[str]:
        """Extract tags from entity metadata for search indexing.

        Handles multiple tag formats:
        - List format: ["tag1", "tag2"]
        - String format: "['tag1', 'tag2']" or "[tag1, tag2]"
        - Empty: [] or "[]"

        Returns a list of tag strings for search indexing.
        """
        if not entity.entity_metadata or "tags" not in entity.entity_metadata:
            return []

        tags = entity.entity_metadata["tags"]

        # Handle list format (preferred)
        if isinstance(tags, list):
            return [str(tag) for tag in tags if tag]

        # Handle string format (legacy)
        if isinstance(tags, str):
            try:
                # Parse string representation of list
                parsed_tags = ast.literal_eval(tags)
                if isinstance(parsed_tags, list):
                    return [str(tag) for tag in parsed_tags if tag]
            except (ValueError, SyntaxError):
                # If parsing fails, treat as single tag
                return [tags] if tags.strip() else []

        return []  # pragma: no cover

    async def index_entity(
        self,
        entity: Entity,
        background_tasks: Optional[BackgroundTasks] = None,
        content: str | None = None,
        *,
        write_embeddings: bool = True,
    ) -> None:
        if background_tasks:
            background_tasks.add_task(self.index_entity_data, entity, content, write_embeddings)
        else:
            await self.index_entity_data(entity, content, write_embeddings)

    async def index_entity_data(
        self,
        entity: Entity,
        content: str | None = None,
        write_embeddings: bool = True,
    ) -> None:
        logger.info(
            f"[BackgroundTask] Starting search index for entity_id={entity.id} "
            f"permalink={entity.permalink} project_id={entity.project_id}"
        )
        try:
            # delete all search index data associated with entity
            await self.repository.delete_by_entity_id(entity_id=entity.id)

            # reindex
            if entity.is_markdown:
                await self.index_entity_markdown(entity, content, write_embeddings)
            else:
                await self.index_entity_file(entity, write_embeddings)

            logger.info(
                f"[BackgroundTask] Completed search index for entity_id={entity.id} "
                f"permalink={entity.permalink}"
            )
        except Exception as e:
            # Background task failure logging; exceptions are re-raised so the
            # caller (sync background task) can record the failure. Covered by
            # test_index_entity_data_reraises_on_repository_error.
            logger.error(
                f"[BackgroundTask] Failed search index for entity_id={entity.id} "
                f"permalink={entity.permalink} error={e}"
            )
            raise

    async def index_entity_file(
        self,
        entity: Entity,
        write_embeddings: bool = True,
    ) -> None:
        # Index entity file with no content
        await self.repository.index_item(
            SearchIndexRow(
                id=entity.id,
                entity_id=entity.id,
                type=SearchItemType.ENTITY.value,
                title=entity.title,
                permalink=entity.permalink,  # Required for Postgres NOT NULL constraint
                file_path=entity.file_path,
                metadata={
                    "entity_type": entity.entity_type,
                },
                created_at=entity.created_at,
                updated_at=_mtime_to_datetime(entity),
                project_id=entity.project_id,
            )
        )
        # Trigger: caller did not suppress embedding writes
        # Why: file entities (binaries) carry no observations/relations, but their
        #      title + permalink are still useful semantic matches
        # Outcome: writes a single entity vector for the file
        if write_embeddings:
            await self._upsert_entity_embeddings(entity, None)

    async def index_entity_markdown(
        self,
        entity: Entity,
        content: str | None = None,
        write_embeddings: bool = True,
    ) -> None:
        """Index an entity and all its observations and relations.

        Args:
            entity: The entity to index
            content: Optional pre-loaded content (avoids file read). If None, will read from file.

        Indexing structure:
        1. Entities
           - permalink: direct from entity (e.g., "specs/search")
           - file_path: physical file location
           - project_id: project context for isolation

        2. Observations
           - permalink: entity permalink + /observations/id (e.g., "specs/search/observations/123")
           - file_path: parent entity's file (where observation is defined)
           - project_id: inherited from parent entity

        3. Relations (only index outgoing relations defined in this file)
           - permalink: from_entity/relation_type/to_entity (e.g., "specs/search/implements/features/search-ui")
           - file_path: source entity's file (where relation is defined)
           - project_id: inherited from source entity

        Each type gets its own row in the search index with appropriate metadata.
        The project_id is automatically added by the repository when indexing.
        """

        # Collect all search index rows to batch insert at the end
        rows_to_index = []

        content_stems = []
        content_snippet = ""
        title_variants = self._generate_variants(entity.title)
        content_stems.extend(title_variants)

        # Use provided content or read from file
        if content is None:
            content = await self.file_service.read_entity_content(entity)
        if content:
            content_stems.append(content)
            content_snippet = f"{content[:250]}"

        if entity.permalink:
            content_stems.extend(self._generate_variants(entity.permalink))

        content_stems.extend(self._generate_variants(entity.file_path))

        # Add entity tags from frontmatter to search content
        entity_tags = self._extract_entity_tags(entity)
        if entity_tags:
            content_stems.extend(entity_tags)

        entity_content_stems = "\n".join(p for p in content_stems if p and p.strip())

        # Truncate to stay under Postgres's 8KB index row limit
        if len(entity_content_stems) > MAX_CONTENT_STEMS_SIZE:  # pragma: no cover
            entity_content_stems = entity_content_stems[:MAX_CONTENT_STEMS_SIZE]  # pragma: no cover

        # Add entity row
        rows_to_index.append(
            SearchIndexRow(
                id=entity.id,
                type=SearchItemType.ENTITY.value,
                title=entity.title,
                content_stems=entity_content_stems,
                content_snippet=content_snippet,
                permalink=entity.permalink,
                file_path=entity.file_path,
                entity_id=entity.id,
                metadata={
                    "entity_type": entity.entity_type,
                },
                created_at=entity.created_at,
                updated_at=_mtime_to_datetime(entity),
                project_id=entity.project_id,
            )
        )

        # Add observation rows - dedupe by permalink to avoid unique constraint violations
        # Two observations with same entity/category/content generate identical permalinks
        seen_permalinks: set[str] = {entity.permalink} if entity.permalink else set()
        for obs in entity.observations:
            obs_permalink = obs.permalink
            if obs_permalink in seen_permalinks:
                logger.debug(f"Skipping duplicate observation permalink: {obs_permalink}")
                continue
            seen_permalinks.add(obs_permalink)

            # Index with parent entity's file path since that's where it's defined
            obs_content_stems = "\n".join(
                p for p in self._generate_variants(obs.content) if p and p.strip()
            )
            # Truncate to stay under Postgres's 8KB index row limit
            if len(obs_content_stems) > MAX_CONTENT_STEMS_SIZE:  # pragma: no cover
                obs_content_stems = obs_content_stems[:MAX_CONTENT_STEMS_SIZE]  # pragma: no cover
            rows_to_index.append(
                SearchIndexRow(
                    id=obs.id,
                    type=SearchItemType.OBSERVATION.value,
                    title=f"{obs.category}: {obs.content[:100]}...",
                    content_stems=obs_content_stems,
                    content_snippet=obs.content,
                    permalink=obs_permalink,
                    file_path=entity.file_path,
                    category=obs.category,
                    entity_id=entity.id,
                    metadata={
                        "tags": obs.tags,
                    },
                    created_at=entity.created_at,
                    updated_at=_mtime_to_datetime(entity),
                    project_id=entity.project_id,
                )
            )

        # Add relation rows (only outgoing relations defined in this file)
        for rel in entity.outgoing_relations:
            # Create descriptive title showing the relationship
            relation_title = (
                f"{rel.from_entity.title} → {rel.to_entity.title}"
                if rel.to_entity
                else f"{rel.from_entity.title}"
            )

            rel_content_stems = "\n".join(
                p for p in self._generate_variants(relation_title) if p and p.strip()
            )
            rows_to_index.append(
                SearchIndexRow(
                    id=rel.id,
                    title=relation_title,
                    permalink=rel.permalink,
                    content_stems=rel_content_stems,
                    file_path=entity.file_path,
                    type=SearchItemType.RELATION.value,
                    entity_id=entity.id,
                    from_id=rel.from_id,
                    to_id=rel.to_id,
                    relation_type=rel.relation_type,
                    created_at=entity.created_at,
                    updated_at=_mtime_to_datetime(entity),
                    project_id=entity.project_id,
                )
            )

        # Batch insert all rows at once
        await self.repository.bulk_index_items(rows_to_index)

        # Write semantic vectors for this entity and its observations/relations
        # (best-effort; no-op when embeddings are disabled). Done after the FTS
        # rows are committed so a vector failure can't leave the keyword index
        # half-written. Suppressed during a full reindex, which backfills all
        # vectors in batched chunks instead of one model call per note.
        if write_embeddings:
            await self._upsert_entity_embeddings(entity, content)

    async def delete_by_permalink(self, permalink: str):
        """Delete an item from the search index."""
        await self.repository.delete_by_permalink(permalink)

    async def delete_by_entity_id(self, entity_id: int):
        """Delete an item from the search index."""
        await self.repository.delete_by_entity_id(entity_id)

    async def handle_delete(self, entity: Entity):
        """Handle complete entity deletion from search index including observations and relations.

        This replicates the logic from sync_service.handle_delete() to properly clean up
        all search index entries for an entity and its related data.
        """
        logger.debug(
            f"Cleaning up search index for entity_id={entity.id}, file_path={entity.file_path}, "
            f"observations={len(entity.observations)}, relations={len(entity.outgoing_relations)}"
        )

        # Clean up search index - same logic as sync_service.handle_delete()
        permalinks = (
            [entity.permalink]
            + [o.permalink for o in entity.observations]
            + [r.permalink for r in entity.outgoing_relations]
        )

        logger.debug(
            f"Deleting search index entries for entity_id={entity.id}, "
            f"index_entries={len(permalinks)}"
        )

        for permalink in permalinks:
            if permalink:
                await self.delete_by_permalink(permalink)
            else:
                await self.delete_by_entity_id(entity.id)

        # Drop the semantic vectors too (entity + observations + relations), so
        # deleted content stops matching. Routed through the shared cleanup helper
        # that the sync file-delete path also uses.
        await self.delete_entity_embeddings(entity)

    async def hybrid_search(
        self,
        query_text: str,
        mode: str,
        limit: int,
        session_maker,
        project_id: int,
    ) -> List[SearchIndexRow]:
        """Perform semantic or hybrid search."""
        if mode == "fts":
            return await self.search(SearchQuery(text=query_text), limit=limit)

        # Prefer the injected (cached) service; fall back to building one from the
        # caller-supplied session_maker/project_id. Either way the ONNX model is
        # loaded at most once per process via the module-level provider cache.
        embedding_service = await self._get_embedding_service()
        if embedding_service is None:
            embedding_service = EmbeddingService.maybe_create(session_maker, project_id)
        if not embedding_service:
            raise ValueError(
                "Embeddings are disabled or not installed. "
                "Set MEMOPAD_EMBEDDINGS_ENABLED=true and install memopad[embeddings]."
            )

        # 1. Semantic hits — mixed item types (entity / observation / relation),
        #    ranked by cosine similarity. Each hit carries its (item_type, item_id).
        semantic_hits = await embedding_service.similar(query_text, limit=limit * 2)
        semantic_ranking = [(hit.item_type, hit.item_id) for hit in semantic_hits]

        # 2. FTS hits if mode is hybrid — also mixed types. We dedupe by (type, id)
        #    so a note that matches as both an entity row and an observation row
        #    doesn't double-count in fusion.
        fts_ranking: list[tuple[str, int]] = []
        fts_rows_by_key: dict[tuple[str, int], SearchIndexRow] = {}
        if mode == "hybrid":
            fts_results = await self.search(SearchQuery(text=query_text), limit=limit * 2)
            for r in fts_results:
                key = (r.type, r.id)
                if key in fts_rows_by_key:
                    continue
                fts_rows_by_key[key] = r
                fts_ranking.append(key)

        # 3. Fuse rankings over item keys. RRF is key-agnostic (any hashable key),
        #    so entity/observation/relation ids fuse on equal footing. For
        #    semantic-only mode we keep the raw similarity scores (no fusion).
        if mode == "hybrid":
            fused = EmbeddingService.reciprocal_rank_fusion([semantic_ranking, fts_ranking])
        else:
            fused = [((hit.item_type, hit.item_id), hit.score) for hit in semantic_hits]

        top_fused = fused[:limit]
        if not top_fused:
            return []

        # 4. Reconstruct SearchIndexRows. FTS already produced rows for any key that
        #    matched by keyword; semantic-only hits (and semantic-only mode) are
        #    fetched from the search_index by (type, id). Items missing from the
        #    index (e.g. a vector whose note was deleted) are silently dropped.
        keys = [key for key, _ in top_fused]
        rows_by_key = dict(fts_rows_by_key)
        missing = [k for k in keys if k not in rows_by_key]
        if missing:
            rows_by_key.update(await self._fetch_index_rows_by_keys(session_maker, missing))

        results = []
        for key, score in top_fused:
            row = rows_by_key.get(key)
            if row:
                row.score = score
                results.append(row)
        return results

    async def _fetch_index_rows_by_keys(
        self,
        session_maker,
        keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], SearchIndexRow]:
        """Fetch search_index rows for a set of (type, id) keys.

        Used by hybrid_search to reconstruct semantic-only hits that didn't also
        match by keyword. Groups by type so each lookup is one ``IN (...)`` query.
        """
        by_type: dict[str, list[int]] = {}
        for item_type, item_id in keys:
            by_type.setdefault(item_type, []).append(item_id)

        out: dict[tuple[str, int], SearchIndexRow] = {}
        async with db.scoped_session(session_maker) as session:
            for item_type, ids in by_type.items():
                result = await session.execute(
                    text(
                        "SELECT id, type, title, permalink, file_path, entity_id, "
                        "from_id, to_id, relation_type, category, content_snippet, "
                        "created_at, updated_at, project_id, metadata "
                        "FROM search_index "
                        "WHERE project_id = :pid AND type = :t AND id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"pid": self.repository.project_id, "t": item_type, "ids": ids},
                )
                for row in result.fetchall():
                    # metadata is JSON text on SQLite, a dict on Postgres (JSONB).
                    md = row[14]
                    if isinstance(md, str):
                        md = json.loads(md) if md else {}
                    elif md is None:
                        md = {}
                    r = SearchIndexRow(
                        project_id=row[13],
                        id=row[0],
                        type=row[1],
                        file_path=row[4],
                        created_at=row[11],
                        updated_at=row[12],
                        title=row[2],
                        permalink=row[3],
                        entity_id=row[5],
                        from_id=row[6],
                        to_id=row[7],
                        relation_type=row[8],
                        category=row[9],
                        content_snippet=row[10],
                        metadata=md,
                    )
                    out[(r.type, r.id)] = r
        return out
