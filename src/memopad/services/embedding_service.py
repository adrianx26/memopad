"""Semantic embedding service for hybrid search.

Provides a small abstraction over an embedding model so the rest of the codebase
doesn't need to know which provider is in use. The default provider uses
`fastembed` (a local, CPU-friendly ONNX runtime) — installed via the optional
`embeddings` extra. When the extra isn't installed the service is disabled and
all operations become no-ops.

The embedding store is a simple SQLite table managed via raw SQL so no Alembic
migration is required for the optional feature. The schema is created lazily on
first write and is a peer of the FTS5 search_index table.

Hybrid search uses Reciprocal Rank Fusion (RRF) to combine BM25 results from
the FTS5 index with cosine-similarity results from this store. RRF is robust to
score-scale differences between the two retrievers and avoids the need to tune
weights per dataset.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Sequence

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db


# --- Configuration ---

EMBEDDINGS_ENABLED_ENV = "MEMOPAD_EMBEDDINGS_ENABLED"
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM_DEFAULT = 384  # bge-small-en-v1.5 produces 384-d vectors


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

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self.model_name = model_name
        # The first call downloads the model (~30MB for bge-small) and caches it.
        self._model = TextEmbedding(model_name=model_name)
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


def _get_provider(model_name: str = DEFAULT_MODEL_NAME) -> EmbeddingProvider:
    """Return a process-cached provider, loading the model only on first use."""
    cached = _PROVIDER_CACHE.get(model_name)
    if cached is None:
        cached = FastEmbedProvider(model_name)
        _PROVIDER_CACHE[model_name] = cached
    return cached


def reset_provider_cache() -> None:
    """Drop cached providers. Used by tests and forced reloads."""
    _PROVIDER_CACHE.clear()


# --- Storage helpers ---


@dataclass
class EmbeddingHit:
    """One result from a similarity search."""

    entity_id: int
    score: float


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

    The store is a single SQLite table:
        embedding(entity_id INTEGER PK, project_id INTEGER, model TEXT,
                  dim INTEGER, vector BLOB, updated_at TEXT)

    A composite index on (project_id, model) lets us scope queries cheaply.
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
        # Trigger: creating the table is async and idempotent, but running it on
        # every upsert/similar is wasteful. Track initialization so it happens once.
        self._store_initialized = False

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

    async def _ensure_store(self) -> None:
        """Create the embedding table once, lazily, on first use."""
        if self._store_initialized:
            return
        await self.init_store()
        self._store_initialized = True

    async def init_store(self) -> None:
        """Create the embedding table if it doesn't exist. Idempotent."""
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS embedding (
                        entity_id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        dim INTEGER NOT NULL,
                        vector BLOB NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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

    async def upsert(self, entity_id: int, content: str) -> None:
        """Embed `content` and write it to the store, replacing any prior row."""
        if not self.provider:
            return
        await self._ensure_store()
        vec = self.provider.embed([content])[0]
        blob = _pack_vector(vec)

        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO embedding (entity_id, project_id, model, dim, vector, updated_at)
                    VALUES (:eid, :pid, :model, :dim, :vec, datetime('now'))
                    ON CONFLICT(entity_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        model = excluded.model,
                        dim = excluded.dim,
                        vector = excluded.vector,
                        updated_at = excluded.updated_at
                    """
                ),
                {
                    "eid": entity_id,
                    "pid": self.project_id,
                    "model": self.provider.model_name,
                    "dim": self.provider.dim,
                    "vec": blob,
                },
            )
            await session.commit()

    async def delete(self, entity_id: int) -> None:
        """Remove an entity's embedding (called when an entity is deleted)."""
        if not self.provider:
            return
        await self._ensure_store()
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("DELETE FROM embedding WHERE entity_id = :eid"),
                {"eid": entity_id},
            )
            await session.commit()

    async def similar(self, query: str, limit: int = 10) -> list[EmbeddingHit]:
        """Return the top-K most similar entities to `query` by cosine score.

        Implementation note: we score in Python because SQLite has no native
        vector ops. For large collections this won't scale; the long-term plan
        is to swap in sqlite-vec or a column-oriented store. For now, this is
        fine — the bottleneck is embedding the query, not the dot products.
        """
        if not self.provider:
            return []
        await self._ensure_store()
        q_vec = self.provider.embed([query])[0]

        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT entity_id, vector FROM embedding "
                    "WHERE project_id = :pid AND model = :model"
                ),
                {"pid": self.project_id, "model": self.provider.model_name},
            )
            rows = result.fetchall()

        scored = [
            EmbeddingHit(entity_id=row[0], score=_cosine(q_vec, _unpack_vector(row[1])))
            for row in rows
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]

    @staticmethod
    def reciprocal_rank_fusion(
        rankings: Iterable[Sequence[int]], k: int = 60
    ) -> list[tuple[int, float]]:
        """Fuse multiple ranked lists of entity_ids via Reciprocal Rank Fusion.

        RRF score for an item = sum over rankings of 1 / (k + rank).
        k=60 is the value from the original RRF paper and works well in practice.
        Items not appearing in a ranking simply contribute zero from that source.

        Returns a list of (entity_id, score) sorted by score desc.
        """
        scores: dict[int, float] = {}
        for ranking in rankings:
            for rank, eid in enumerate(ranking):
                scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + rank + 1)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
