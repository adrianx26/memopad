"""Semantic embedding service for hybrid search.

Provides a small abstraction over an embedding model so the rest of the codebase
doesn't need to know which provider is in use. The default provider uses
`fastembed` (a local, CPU-friendly ONNX runtime) — installed via the optional
`embeddings` extra. When the extra isn't installed the service is disabled and
all operations become no-ops.

Vectors are keyed by ``(item_type, item_id)`` so that entities, observations
(facts), and relations can all be embedded — semantic search surfaces a specific
fact or relationship, not just the parent note. ``item_type`` is one of
``entity`` / ``observation`` / ``relation`` and ``item_id`` is the corresponding
table's primary key (ids collide across those tables, hence the composite key).

Storage is two-tier:

1. **Canonical BLOB store** — a single SQLite/Postgres ``embedding`` table holding
   packed float32 vectors. Portable, works on every backend, and the source of
   truth. Created lazily on first write (and by the ``m6f7a8b9c0d1`` migration).
2. **Optional ``sqlite-vec`` ANN index** — on the default SQLite backend, when the
   ``sqlite-vec`` extension loads, a ``vec0`` virtual table *per item type per
   project* mirrors the BLOB store for sublinear KNN. The service writes to both
   (the BLOB insert is cheap; embedding inference dominates) and queries vec0 when
   available, falling back to a numpy-vectorized cosine over the BLOB store
   otherwise. ``numpy`` ships with ``fastembed``, so the fallback is fast to
   ~100k+ vectors; vec0 scales to millions.

Hybrid search uses Reciprocal Rank Fusion (RRF) to combine BM25 results from the
FTS5 index with similarity results from this store. RRF is robust to score-scale
differences between the two retrievers and avoids the need to tune weights per
dataset.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import struct
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Optional, Protocol, Sequence, TypeVar

from loguru import logger
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db


# --- Configuration ---

EMBEDDINGS_ENABLED_ENV = "MEMOPAD_EMBEDDINGS_ENABLED"
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM_DEFAULT = 384  # bge-small-en-v1.5 produces 384-d vectors

# ONNX intra-op thread cap for the embedding model. fastembed/onnxruntime defaults
# to one thread per logical CPU, so a single embed() call saturates every core
# and — because it runs sync on the event loop (see _embed) — freezes the server
# while it does. Capping to a small default keeps the server responsive during
# indexing/search and bounds CPU spikes; override with the env var (0 = let
# onnxruntime decide, i.e. all cores).
EMBEDDING_THREADS_ENV = "MEMOPAD_EMBEDDING_THREADS"
DEFAULT_NUM_THREADS = min(4, os.cpu_count() or 1)

# Bound on the LRU query-embedding cache (Fix 5). Repeat identical queries are
# served from cache without a model call; bounded so a query-flood can't grow
# memory unbounded. Keyed by (model_name, query).
QUERY_CACHE_MAX = 256

# Item types stored in the embedding table. Each maps to its own vec0 table on the
# ANN path so the integer `id` (= the item's table PK) is unique within a table.
ITEM_TYPE_ENTITY = "entity"
ITEM_TYPE_OBSERVATION = "observation"
ITEM_TYPE_RELATION = "relation"
_ITEM_TYPES = (ITEM_TYPE_ENTITY, ITEM_TYPE_OBSERVATION, ITEM_TYPE_RELATION)

# Default chunk size for batched backfill (one model call per chunk).
BACKFILL_BATCH_DEFAULT = 128

# numpy is a fastembed dependency, so it's present whenever embeddings are enabled.
# Imported lazily so this module stays importable without the extra (unit tests for
# the pure helpers run without numpy/fastembed installed).
# Declared up front (Any) so the optional-import block below keeps a single,
# always-bound name pyright is happy with — the numpy module when available,
# ``None`` otherwise. The numpy scoring path is only entered when _HAS_NUMPY.
_np: Any
try:
    import numpy as _np  # type: ignore[import-not-found]

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover - numpy present whenever fastembed is
    _np = None
    _HAS_NUMPY = False


# Reciprocal Rank Fusion is generic over the ranking key type so callers get
# back their own key type (e.g. ``tuple[str, int]``) rather than a widened
# ``Hashable``. Module-scoped: class-body names are not lexically visible inside
# methods, so a TypeVar used in a method must live at module level.
_K = TypeVar("_K", bound=Hashable)


def is_enabled() -> bool:
    """Return True iff embeddings are explicitly enabled and the provider is available.

    The env-var gate prevents the service from spinning up a model on import for
    users who don't want it. Existing installs see no behavior change.
    """
    if os.environ.get(EMBEDDINGS_ENABLED_ENV, "").lower() not in ("1", "true", "yes"):
        return False
    try:
        import fastembed  # noqa: F401
    except ImportError:
        logger.warning(
            "Embeddings enabled but `fastembed` is not installed. "
            "Install with: pip install 'memopad[embeddings]'"
        )
        return False
    return True


# --- Provider protocol ---


class EmbeddingProvider(Protocol):
    """Protocol any embedding backend must satisfy."""

    model_name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts. Order of the output matches the input."""
        ...


class FastEmbedProvider:
    """fastembed-backed provider.

    fastembed is a CPU-only inference library that ships ONNX models. It avoids
    pulling in torch/transformers, which would more than double the install size.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        num_threads: Optional[int] = None,
    ):
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self.model_name = model_name
        # Cap ONNX intra-op threads so a single embed() call doesn't peg every
        # core (fastembed defaults to cpu_count threads). `threads=None` leaves
        # the decision to fastembed (the legacy behavior).
        kwargs: dict[str, Any] = {}
        if num_threads is not None:
            kwargs["threads"] = num_threads
        # The first call downloads the model (~30MB for bge-small) and caches it.
        self._model = TextEmbedding(model_name=model_name, **kwargs)
        # We only learn the true dim after the first inference; assume default
        # for the canonical model and validate once on first use.
        self.dim = EMBEDDING_DIM_DEFAULT

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # fastembed returns a generator of numpy arrays; cast to plain lists so
        # downstream code doesn't depend on numpy.
        vectors = list(self._model.embed(list(texts)))
        out: list[list[float]] = []
        for vec in vectors:
            out.append([float(x) for x in vec])
        if out and len(out[0]) != self.dim:
            self.dim = len(out[0])
        return out


# --- Provider cache ---

# Trigger: the ONNX model is expensive to load (download + JIT compile).
# Why: long-running servers and CLI backfills call embed() many times; reloading
#      the model per call (as the original code did via a fresh FastEmbedProvider
#      on every hybrid_search) dominated latency. Caching at module scope means
#      the model loads once per process and `maybe_create` becomes a dict lookup.
_PROVIDER_CACHE: dict[str, EmbeddingProvider] = {}


def _resolve_num_threads() -> Optional[int]:
    """Resolve the ONNX thread cap from the env, or the small default.

    ``MEMOPAD_EMBEDDING_THREADS=0`` explicitly restores the legacy "let
    onnxruntime use all cores" behavior; an unset var yields the capped default
    that keeps the server responsive.
    """
    raw = os.environ.get(EMBEDDING_THREADS_ENV)
    if raw is None or raw == "":
        return DEFAULT_NUM_THREADS
    try:
        val = int(raw)
    except ValueError:
        logger.warning(
            f"Invalid {EMBEDDING_THREADS_ENV}={raw!r}; using default {DEFAULT_NUM_THREADS}"
        )
        return DEFAULT_NUM_THREADS
    return None if val <= 0 else val


def _get_provider(model_name: str = DEFAULT_MODEL_NAME) -> EmbeddingProvider:
    """Return a process-cached provider, loading the model only on first use."""
    cached = _PROVIDER_CACHE.get(model_name)
    if cached is None:
        cached = FastEmbedProvider(model_name, num_threads=_resolve_num_threads())
        _PROVIDER_CACHE[model_name] = cached
    return cached


def reset_provider_cache() -> None:
    """Drop cached providers and the query-embedding cache. Used by tests/reloads."""
    _PROVIDER_CACHE.clear()
    _QUERY_EMBED_CACHE.clear()


# --- Storage helpers ---


@dataclass
class EmbeddingHit:
    """One result from a similarity search."""

    item_type: str
    item_id: int
    score: float


def _content_hash(text: str) -> str:
    """Stable SHA-256 of the embedded text, used to skip re-embedding unchanged items.

    Stored alongside each vector so ``upsert_batch`` can compare the text we're
    about to embed against the text already embedded for that (item_type,
    item_id) key. A match means the vector is still valid and the expensive
    model call is skipped. The hash also covers the ``model`` column check
    (handled in ``upsert_batch``) so a model swap re-embeds even if the text is
    unchanged.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Bounded LRU cache of query → query embedding (Fix 5). ``similar`` embeds the
# query string on every call; repeated identical queries (common in UIs/MCP)
# pay full inference each time. Keyed by (model_name, query) so a model swap
# can't serve a stale vector. asyncio is single-threaded, so the OrderedDict
# needs no locking.
_QUERY_EMBED_CACHE: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def _cached_query_vec(model: str, query: str) -> Optional[list[float]]:
    """Return the cached query embedding, or None on a miss."""
    key = (model, query)
    vec = _QUERY_EMBED_CACHE.get(key)
    if vec is not None:
        _QUERY_EMBED_CACHE.move_to_end(key)
    return vec


def _store_query_vec(model: str, query: str, vec: list[float]) -> None:
    """Cache a query embedding, evicting the least-recently-used above the cap."""
    key = (model, query)
    _QUERY_EMBED_CACHE[key] = vec
    _QUERY_EMBED_CACHE.move_to_end(key)
    while len(_QUERY_EMBED_CACHE) > QUERY_CACHE_MAX:
        _QUERY_EMBED_CACHE.popitem(last=False)


def _pack_vector(vec: Sequence[float]) -> bytes:
    """Pack a float32 vector to bytes for compact storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> list[float]:
    """Unpack bytes back into a float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Assumes equal length; returns 0.0 if either is zero."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# --- Service ---


class EmbeddingService:
    """Manages the embedding store and similarity search.

    Canonical store (all backends):

        embedding(item_type TEXT, item_id INTEGER, project_id INTEGER, model TEXT,
                  dim INTEGER, vector BLOB, content_hash TEXT, updated_at TEXT,
                  PRIMARY KEY (item_type, item_id))

    ``content_hash`` (SHA-256 of the embedded text) lets ``upsert_batch`` skip
    re-embedding items whose text is unchanged. An index on (project_id, model)
    scopes BLOB-path queries cheaply.

    ANN index (SQLite + sqlite-vec only): one ``vec0`` virtual table per item type
    per project, ``embedding_vec_{item_type}_p{project_id}``, with ``id = item_id``
    and a cosine-distance vector column. KNN scans only that project+type's vectors
    (vec0 0.1.x disallows auxiliary-column filters inside a KNN query, so we scope
    by table rather than by WHERE).
    """

    def __init__(
        self,
        session_maker: async_sessionmaker,
        project_id: int,
        provider: Optional[EmbeddingProvider] = None,
    ):
        self.session_maker = session_maker
        self.project_id = project_id
        self.provider = provider
        # Trigger: creating tables is async and idempotent, but running it on
        # every upsert/similar is wasteful. Track initialization so it happens once.
        self._store_initialized = False
        # Resolved on init_store: True when the sqlite-vec vec0 index is available.
        self._use_vec0 = False

    @classmethod
    def maybe_create(
        cls,
        session_maker: async_sessionmaker,
        project_id: int,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> Optional["EmbeddingService"]:
        """Build a service only if embeddings are enabled & available.

        Uses the process-cached provider so the model loads at most once per
        process regardless of how often this is called.
        """
        if not is_enabled():
            return None
        provider = _get_provider(model_name)
        return cls(session_maker, project_id, provider)

    # --- store lifecycle ---

    async def _ensure_store(self) -> None:
        """Create the embedding tables once, lazily, on first use."""
        if self._store_initialized:
            return
        await self.init_store()
        self._store_initialized = True

    async def init_store(self) -> None:
        """Create the BLOB table (always) and vec0 tables (if sqlite-vec loads).

        Idempotent. The BLOB table is also created by the m6f7a8b9c0d1 migration;
        we recreate it lazily so test databases that skip migrations still work.
        """
        await self._init_blob_store()
        # Best-effort ANN index. A failure here (extension missing, unsupported
        # platform, version mismatch) is non-fatal: the BLOB + numpy path still
        # serves queries, just without sublinear scaling.
        self._use_vec0 = await self._try_init_vec0_store()
        if self._use_vec0:  # pragma: no cover - requires sqlite-vec extension
            logger.debug(f"Embedding ANN index enabled (vec0) for project_id={self.project_id}")
        else:
            logger.debug(
                f"Embedding ANN index unavailable; using BLOB + "
                f"{'numpy' if _HAS_NUMPY else 'python'} scoring "
                f"for project_id={self.project_id}"
            )

    async def _init_blob_store(self) -> None:
        """Create the canonical embedding BLOB table and its project/model index.

        ``content_hash`` (Fix 2) stores the SHA-256 of the embedded text so
        ``upsert_batch`` can skip re-embedding items whose text is unchanged.
        Added by the ``p9d1e2f3a4b5`` migration; included in the CREATE here so
        fresh databases (and test fixtures that skip Alembic) get the column
        without the migration.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS embedding (
                        item_type TEXT NOT NULL,
                        item_id INTEGER NOT NULL,
                        project_id INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        dim INTEGER NOT NULL,
                        vector BLOB NOT NULL,
                        content_hash TEXT,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (item_type, item_id)
                    )
                    """
                )
            )
            await session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_embedding_project_model "
                    "ON embedding(project_id, model)"
                )
            )
            await session.commit()
        await self._ensure_content_hash_column()

    async def _ensure_content_hash_column(self) -> None:
        """Add ``content_hash`` to an existing ``embedding`` table if absent.

        For databases created before the column was added that then skip the
        migration (some test fixtures), the CREATE TABLE IF NOT EXISTS above is
        a no-op and the column stays missing. This backfills it. Run in its own
        session/transaction so a "duplicate column" error can't roll back the
        table creation above. The migration is the primary path; this is the
        lazy fallback.
        """
        if await self._has_content_hash_column():
            return
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("ALTER TABLE embedding ADD COLUMN content_hash TEXT")
            )
            await session.commit()

    async def _has_content_hash_column(self) -> bool:
        """Return True iff the ``embedding`` table has a ``content_hash`` column."""
        async with db.scoped_session(self.session_maker) as session:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                result = await session.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'embedding' AND column_name = 'content_hash'"
                    )
                )
                return result.fetchone() is not None
            # SQLite: column name is the 2nd field of PRAGMA table_info.
            result = await session.execute(text("PRAGMA table_info(embedding)"))
            return any(row[1] == "content_hash" for row in result.fetchall())

    def _vec_table(self, item_type: str) -> str:
        """Name of the vec0 table for a given item type + model dim in this project.

        Scoped by the provider's vector dimension so a model swap (which usually
        changes the dimension, e.g. bge-small 384 → bge-base 768) writes to a fresh
        vec0 table instead of INSERT-ing wrong-dimension vectors into the old
        table. Without the dim suffix the old-dim table persists (``IF NOT
        EXISTS`` is a no-op), the wrong-dim INSERT raises inside the same
        transaction as the canonical BLOB write, and the whole transaction rolls
        back — so after a model swap no embeddings would advance at all (and the
        broad ``except`` in SearchService would swallow it silently). The old-dim
        table is left in place (a space leak, never queried again); a ``--force``
        reindex rebuilds the current-dim table.

        Note: two *same-dimension* but different models would still share a table
        and blend in KNN. The process runs one model at a time, so this is not a
        live concern; a future per-model suffix would need a slug + a data
        migration to rebuild existing vec0 rows.
        """
        dim = self.provider.dim if self.provider else EMBEDDING_DIM_DEFAULT
        return f"embedding_vec_{item_type}_p{self.project_id}_d{dim}"

    async def _try_init_vec0_store(self) -> bool:
        """Create one vec0 table per item type. Return True iff all succeeded."""
        if self.provider is None:
            return False
        dim = self.provider.dim
        ddl = [
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._vec_table(t)} "
            f"USING vec0(id integer primary key, embedding float[{dim}] distance_metric=cosine)"
            for t in _ITEM_TYPES
        ]
        try:
            async with db.scoped_session(self.session_maker) as session:
                for stmt in ddl:
                    await session.execute(text(stmt))
                await session.commit()
            return True  # pragma: no cover - requires sqlite-vec extension
        except Exception as e:  # pragma: no cover - environment-gated (extension absent)
            logger.debug(f"sqlite-vec vec0 init skipped: {e}")
            return False

    # --- writes ---

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Run ONNX inference on a worker thread so it can't block the event loop.

        ``provider.embed`` is a synchronous, CPU-heavy ONNX call; calling it
        directly inside an ``async def`` stalls the whole asyncio loop for the
        duration of inference (and, with onnxruntime's default thread count,
        pegs every core). ``asyncio.to_thread`` moves the work to the default
        executor so the loop keeps serving other requests while a batch embeds.
        """
        if not self.provider or not texts:
            return []
        return await asyncio.to_thread(self.provider.embed, list(texts))

    async def _fetch_hashes(
        self, keys: Sequence[tuple[str, int]]
    ) -> dict[tuple[str, int], tuple[Optional[str], Optional[str]]]:
        """Return ``{(item_type, item_id): (content_hash, model)}`` for existing rows.

        Missing keys are absent from the result. Used by ``upsert_batch`` to
        decide which items still need embedding: a key needs (re-)embedding iff
        it is new, or its stored ``content_hash``/``model`` differs from the
        current text/model.
        """
        by_type: dict[str, list[int]] = {}
        for item_type, item_id in keys:
            by_type.setdefault(item_type, []).append(item_id)
        out: dict[tuple[str, int], tuple[Optional[str], Optional[str]]] = {}
        async with db.scoped_session(self.session_maker) as session:
            for item_type, ids in by_type.items():
                result = await session.execute(
                    text(
                        "SELECT item_id, content_hash, model FROM embedding "
                        "WHERE item_type = :t AND item_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"t": item_type, "ids": ids},
                )
                for row in result.fetchall():
                    out[(item_type, row[0])] = (row[1], row[2])
        return out

    async def upsert(self, item_type: str, item_id: int, content: str) -> None:
        """Embed `content` and write it, replacing any prior vector for this item."""
        await self.upsert_batch([(item_type, item_id, content)])

    async def upsert_batch(self, items: Sequence[tuple[str, int, str]]) -> None:
        """Embed a batch of items in one model call and write all vectors.

        Skips items whose embedded text is unchanged since the last write
        (Fix 2): the SHA-256 of each text is compared against the stored
        ``content_hash`` (and the stored ``model``), so a re-index of unchanged
        content does zero model work. Only new/changed items are embedded.

        Inference runs off the event loop via ``_embed`` (Fix 1).

        Args:
            items: sequence of (item_type, item_id, text). Order preserved through
                embedding so texts and keys stay aligned.
        """
        if not self.provider or not items:
            return
        await self._ensure_store()

        # Pre-compute the content hash for every item and look up what's stored.
        hashed = [(t, iid, txt, _content_hash(txt)) for (t, iid, txt) in items]
        existing = await self._fetch_hashes([(t, iid) for (t, iid, _txt, _h) in hashed])
        cur_model = self.provider.model_name
        to_embed: list[tuple[str, int, str]] = []
        for item_type, item_id, txt, h in hashed:
            prev = existing.get((item_type, item_id))
            if prev is None or prev[0] != h or prev[1] != cur_model:
                to_embed.append((item_type, item_id, txt))
        if not to_embed:
            # Everything in the batch is already current — no model call needed.
            return

        vectors = await self._embed([txt for _, _, txt in to_embed])
        hash_of = {(t, iid): h for (t, iid, _txt, h) in hashed}

        # Canonical BLOB rows (always written, all backends).
        blob_rows = [
            {
                "t": item_type,
                "iid": item_id,
                "pid": self.project_id,
                "model": self.provider.model_name,
                "dim": self.provider.dim,
                "vec": _pack_vector(vector),
                "ch": hash_of[(item_type, item_id)],
            }
            for (item_type, item_id, _), vector in zip(to_embed, vectors)
        ]

        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO embedding
                        (item_type, item_id, project_id, model, dim, vector,
                         content_hash, updated_at)
                    VALUES (:t, :iid, :pid, :model, :dim, :vec, :ch, datetime('now'))
                    ON CONFLICT(item_type, item_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        model = excluded.model,
                        dim = excluded.dim,
                        vector = excluded.vector,
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at
                    """
                ),
                blob_rows,
            )
            # ANN index mirror: one upsert per item into its type's vec0 table.
            # vec0 has no UPSERT/REPLACE support, so re-embedding an item is a
            # delete-then-insert against its integer id.
            if self._use_vec0:  # pragma: no cover - requires sqlite-vec extension
                for (item_type, item_id, _), vector in zip(to_embed, vectors):
                    await session.execute(
                        text(f"DELETE FROM {self._vec_table(item_type)} WHERE id = :id"),
                        {"id": item_id},
                    )
                    await session.execute(
                        text(
                            f"INSERT INTO {self._vec_table(item_type)} (id, embedding) "
                            f"VALUES (:id, :vec)"
                        ),
                        {"id": item_id, "vec": _pack_vector(vector)},
                    )
            await session.commit()

    async def delete_batch(self, keys: Sequence[tuple[str, int]]) -> None:
        """Remove vectors for a batch of (item_type, item_id) keys."""
        if not self.provider or not keys:
            return
        await self._ensure_store()
        # Group by type so each BLOB delete is a single IN (...) statement and each
        # vec0 delete targets the right table.
        by_type: dict[str, list[int]] = {}
        for item_type, item_id in keys:
            by_type.setdefault(item_type, []).append(item_id)

        async with db.scoped_session(self.session_maker) as session:
            for item_type, ids in by_type.items():
                await session.execute(
                    text(
                        "DELETE FROM embedding WHERE project_id = :pid "
                        "AND item_type = :t AND item_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"pid": self.project_id, "t": item_type, "ids": ids},
                )
                if self._use_vec0:  # pragma: no cover - requires sqlite-vec extension
                    await session.execute(
                        text(
                            f"DELETE FROM {self._vec_table(item_type)} WHERE id IN :ids"
                        ).bindparams(bindparam("ids", expanding=True)),
                        {"ids": ids},
                    )
            await session.commit()

    async def clear_project(self, project_id: Optional[int] = None) -> None:
        """Delete every vector for a project (used by full reindex to drop stale rows).

        Defaults to this service's project_id.
        """
        if not self.provider:
            return
        await self._ensure_store()
        pid = project_id if project_id is not None else self.project_id
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("DELETE FROM embedding WHERE project_id = :pid"),
                {"pid": pid},
            )
            if self._use_vec0:  # pragma: no cover - requires sqlite-vec extension
                for t in _ITEM_TYPES:
                    await session.execute(
                        text(f"DELETE FROM {self._vec_table(t)}"),
                    )
            await session.commit()

    async def prune_project(
        self,
        project_id: Optional[int] = None,
        keep_keys: Optional[set[tuple[str, int]]] = None,
    ) -> None:
        """Drop vectors for this project whose (item_type, item_id) is not in ``keep_keys``.

        Used by the incremental reindex to replace the guarantee ``clear_project``
        gave: vectors for entities/observations/relations deleted since the last
        reindex (whose ids are no longer enumerable) are removed. The set of valid
        keys is recomputed from current entities each run, so this also catches
        id drift on skipped entities.

        Best-effort and a no-op when ``keep_keys`` is None (e.g. embeddings
        disabled). Reuses ``delete_batch`` so both the BLOB store and the vec0
        mirror tables stay consistent.
        """
        if not self.provider or keep_keys is None:
            return
        await self._ensure_store()
        pid = project_id if project_id is not None else self.project_id
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                text("SELECT item_type, item_id FROM embedding WHERE project_id = :pid"),
                {"pid": pid},
            )
            embedded = {(row[0], row[1]) for row in result.fetchall()}
        diff = list(embedded - keep_keys)
        if not diff:
            return
        for i in range(0, len(diff), BACKFILL_BATCH_DEFAULT):
            await self.delete_batch(diff[i : i + BACKFILL_BATCH_DEFAULT])

    # --- reads ---

    async def similar(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
    ) -> list[EmbeddingHit]:
        """Return the top-K most similar items to `query` by cosine similarity.

        Primary path: vec0 KNN (sublinear, scans only this project+type's vectors).
        Fallback: numpy matmul over the BLOB store (fast to ~100k+ vectors), or a
        pure-Python cosine loop if numpy is somehow absent.

        The query is embedded off the event loop via ``_embed`` (Fix 1), and
        repeat queries are served from a bounded LRU cache so identical searches
        skip the model call entirely (Fix 5).
        """
        if not self.provider:
            return []
        await self._ensure_store()
        model = self.provider.model_name
        q_vec = _cached_query_vec(model, query)
        if q_vec is None:
            q_vec = (await self._embed([query]))[0]
            _store_query_vec(model, query, q_vec)

        if self._use_vec0:  # pragma: no cover - requires sqlite-vec extension
            return await self._similar_vec0(q_vec, limit, item_type)
        return await self._similar_blob(q_vec, limit, item_type)

    async def _similar_vec0(
        self,
        q_vec: Sequence[float],
        limit: int,
        item_type: Optional[str],
    ) -> list[EmbeddingHit]:  # pragma: no cover - requires sqlite-vec extension
        """KNN via sqlite-vec. One vec0 table per type; merge across types if unfiltered."""
        packed = _pack_vector(q_vec)
        # Trigger: no item_type filter
        # Why: the caller wants the best matches across all item kinds (entities,
        #      observations, relations) fused together by hybrid search.
        # Outcome: query each type's vec0 table and merge by score
        types = (item_type,) if item_type else _ITEM_TYPES
        hits: list[EmbeddingHit] = []
        async with db.scoped_session(self.session_maker) as session:
            for t in types:
                result = await session.execute(
                    text(
                        f"SELECT id, distance FROM {self._vec_table(t)} "
                        f"WHERE embedding MATCH :q ORDER BY distance LIMIT :k"
                    ),
                    {"q": packed, "k": limit},
                )
                for row in result.fetchall():
                    # cosine distance ∈ [0, 2]; similarity = 1 - distance
                    hits.append(EmbeddingHit(item_type=t, item_id=row[0], score=1.0 - row[1]))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def _similar_blob(
        self,
        q_vec: Sequence[float],
        limit: int,
        item_type: Optional[str],
    ) -> list[EmbeddingHit]:
        """Score every vector for this project in memory and take the top-K.

        numpy matmul when available (one vectorized dot product); pure-Python
        cosine otherwise. The BLOB path is the fallback when sqlite-vec is absent,
        e.g. on Postgres or platforms where the extension won't load.
        """
        model = self.provider.model_name if self.provider else ""
        async with db.scoped_session(self.session_maker) as session:
            if item_type:
                result = await session.execute(
                    text(
                        "SELECT item_type, item_id, vector FROM embedding "
                        "WHERE project_id = :pid AND model = :model AND item_type = :t"
                    ),
                    {"pid": self.project_id, "model": model, "t": item_type},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT item_type, item_id, vector FROM embedding "
                        "WHERE project_id = :pid AND model = :model"
                    ),
                    {"pid": self.project_id, "model": model},
                )
            rows = result.fetchall()

        if not rows:
            return []

        if _HAS_NUMPY:
            return self._score_numpy(q_vec, rows, limit)
        # pragma: no cover - numpy is present whenever embeddings are enabled
        return self._score_python(q_vec, rows, limit)

    @staticmethod
    def _score_numpy(
        q_vec: Sequence[float],
        rows: Sequence[Any],
        limit: int,
    ) -> list[EmbeddingHit]:
        """Vectorized cosine: normalize rows + query, then one matmul."""
        mat = _np.vstack([_np.frombuffer(row[2], dtype=_np.float32) for row in rows])
        mat = mat / _np.linalg.norm(mat, axis=1, keepdims=True).clip(min=1e-12)
        q = _np.asarray(q_vec, dtype=_np.float32)
        q = q / max(float(_np.linalg.norm(q)), 1e-12)
        scores = mat @ q
        order = _np.argsort(-scores)[:limit]
        return [
            EmbeddingHit(item_type=rows[i][0], item_id=rows[i][1], score=float(scores[i]))
            for i in order
        ]

    @staticmethod
    def _score_python(
        q_vec: Sequence[float],
        rows: Sequence[Any],
        limit: int,
    ) -> list[EmbeddingHit]:  # pragma: no cover - numpy is present whenever embeddings are enabled
        """Pure-Python cosine fallback (used only when numpy is unavailable)."""
        scored = [
            EmbeddingHit(
                item_type=row[0], item_id=row[1], score=_cosine(q_vec, _unpack_vector(row[2]))
            )
            for row in rows
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]

    # --- fusion ---

    @staticmethod
    def reciprocal_rank_fusion(
        rankings: Iterable[Sequence[_K]], k: int = 60
    ) -> list[tuple[_K, float]]:
        """Fuse multiple ranked lists of item keys via Reciprocal Rank Fusion.

        Keys are hashable item identifiers — ints (legacy entity-only callers) or
        ``(item_type, item_id)`` tuples (mixed-type hybrid search). RRF score for
        an item = sum over rankings of 1 / (k + rank). k=60 is the value from the
        original RRF paper and works well in practice. Items not appearing in a
        ranking contribute zero from that source.

        Returns a list of (key, score) sorted by score desc. Generic over the key
        type so callers get back their own key type (e.g. ``tuple[str, int]``)
        rather than a widened ``Hashable``.
        """
        scores: dict[_K, float] = {}
        for ranking in rankings:
            for rank, key in enumerate(ranking):
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
