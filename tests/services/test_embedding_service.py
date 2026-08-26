"""Unit tests for the embedding service helpers (no model required)."""

import pytest

from memopad.services.embedding_service import (
    EmbeddingService,
    _cosine,
    _pack_vector,
    _unpack_vector,
    is_enabled,
)


class _FakeProvider:
    """Deterministic embedding provider for backfill round-trip tests."""

    model_name = "fake-model"
    dim = 3

    def embed(self, texts):
        # Map each text to a deterministic 3-vector by simple char codes.
        out = []
        for t in texts:
            vals = [(ord(c) % 7) + 1 for c in t[:3].ljust(3, "\0")]
            out.append([float(v) for v in vals])
        return out


class TestVectorPacking:
    def test_round_trip(self):
        vec = [0.1, -0.2, 3.5, 0.0, 1e-6]
        packed = _pack_vector(vec)
        unpacked = _unpack_vector(packed)
        assert len(unpacked) == len(vec)
        for orig, got in zip(vec, unpacked):
            assert pytest.approx(got, abs=1e-6) == orig

    def test_empty_vector_round_trip(self):
        assert _unpack_vector(_pack_vector([])) == []


class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestReciprocalRankFusion:
    def test_single_ranking_preserves_order(self):
        fused = EmbeddingService.reciprocal_rank_fusion([[10, 20, 30]])
        assert [eid for eid, _ in fused] == [10, 20, 30]

    def test_top_in_both_ranks_first(self):
        # ID 1 ranks #1 in both rankings → must come out on top after fusion.
        fused = EmbeddingService.reciprocal_rank_fusion([[1, 2, 3], [1, 4, 5]])
        assert fused[0][0] == 1

    def test_id_in_only_one_ranking_still_appears(self):
        fused = EmbeddingService.reciprocal_rank_fusion([[1, 2], [3, 4]])
        ids = [eid for eid, _ in fused]
        assert set(ids) == {1, 2, 3, 4}

    def test_empty_input_returns_empty(self):
        assert EmbeddingService.reciprocal_rank_fusion([]) == []


class TestBackfillHelpers:
    """Bug 9: existing_ids / upsert_many power the safe embedding backfill.

    These exercise the real SQL (init_store + insert-missing + replace) against
    an in-memory aiosqlite DB with a deterministic fake provider (no ONNX model).
    """

    @pytest.mark.asyncio
    async def test_upsert_many_then_existing_ids_round_trip(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            svc = EmbeddingService(session_maker, project_id=1, provider=_FakeProvider())

            await svc.init_store()
            assert await svc.existing_ids() == set()

            written = await svc.upsert_many([(1, "abc"), (2, "de"), (3, "fgh")])
            assert written == 3
            assert await svc.existing_ids() == {1, 2, 3}

            # Insert-missing semantics: existing_ids is the backfill skip set.
            written = await svc.upsert_many([(4, "zzz")])
            assert written == 1
            assert await svc.existing_ids() == {1, 2, 3, 4}

            # Re-embedding replaces (ON CONFLICT update) without growing the set.
            written = await svc.upsert_many([(1, "changed content")])
            assert written == 1
            assert await svc.existing_ids() == {1, 2, 3, 4}
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_disabled_provider_is_noop(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            # provider=None mirrors the embeddings-disabled path.
            svc = EmbeddingService(session_maker, project_id=1, provider=None)
            assert await svc.existing_ids() == set()
            assert await svc.upsert_many([(1, "x")]) == 0
        finally:
            await engine.dispose()


class TestIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MEMOPAD_EMBEDDINGS_ENABLED", raising=False)
        assert is_enabled() is False

    def test_explicit_disable(self, monkeypatch):
        monkeypatch.setenv("MEMOPAD_EMBEDDINGS_ENABLED", "false")
        assert is_enabled() is False
