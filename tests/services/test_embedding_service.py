"""Unit tests for the embedding service helpers (no model required)."""

import pytest
from sqlalchemy import text

from memopad.services.embedding_service import (
    ITEM_TYPE_ENTITY,
    ITEM_TYPE_OBSERVATION,
    ITEM_TYPE_RELATION,
    EmbeddingHit,
    EmbeddingService,
    _cosine,
    _pack_vector,
    _unpack_vector,
    is_enabled,
    reset_provider_cache,
)


class _FakeProvider:
    """Deterministic stand-in for the ONNX provider (no fastembed needed).

    Letter-bucket vectors (ord(ch) % dim, L2-normalized) so similar text yields
    similar cosine scores — enough to exercise the store + ranking paths.
    """

    model_name = "fake/deterministic"
    dim = 8

    def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for ch in (t or "").lower():
                if ch.isalpha():
                    vec[ord(ch) % self.dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
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


class TestIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MEMOPAD_EMBEDDINGS_ENABLED", raising=False)
        assert is_enabled() is False

    def test_explicit_disable(self, monkeypatch):
        monkeypatch.setenv("MEMOPAD_EMBEDDINGS_ENABLED", "false")
        assert is_enabled() is False


async def _count_rows(session_maker, project_id, item_type=None, item_id=None):
    """Count embedding rows matching the optional (item_type, item_id) filter.

    A coroutine helper awaited by the store tests below (not a test itself).
    """
    async with session_maker() as session:
        sql = "SELECT item_type, item_id, dim, model FROM embedding WHERE project_id = :pid"
        params: dict = {"pid": project_id}
        if item_type is not None:
            sql += " AND item_type = :t"
            params["t"] = item_type
        if item_id is not None:
            sql += " AND item_id = :iid"
            params["iid"] = item_id
        result = await session.execute(text(sql), params)
        return result.fetchall()


class TestEmbeddingStore:
    """Exercise the canonical BLOB store + numpy scoring path (no sqlite-vec needed).

    These construct EmbeddingService directly with a fake provider, bypassing the
    is_enabled() gate, so they run in CI without the embeddings extra installed.
    """

    async def _service(self, session_maker, project_id):
        svc = EmbeddingService(session_maker, project_id, provider=_FakeProvider())
        await svc.init_store()
        return svc

    @pytest.mark.asyncio
    async def test_upsert_batch_writes_item_type_keyed_rows(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "python web framework"),
                (ITEM_TYPE_OBSERVATION, 7, "category uses flask"),
                (ITEM_TYPE_RELATION, 9, "python depends_on flask"),
            ]
        )

        rows = await _count_rows(session_maker, test_project.id)
        assert {(r[0], r[1]) for r in rows} == {
            (ITEM_TYPE_ENTITY, 1),
            (ITEM_TYPE_OBSERVATION, 7),
            (ITEM_TYPE_RELATION, 9),
        }
        for r in rows:
            assert r[2] == _FakeProvider.dim
            assert r[3] == _FakeProvider.model_name

    @pytest.mark.asyncio
    async def test_item_type_keying_avoids_id_collision(self, session_maker, test_project):
        """Entity id 1 and observation id 1 must coexist (composite PK)."""
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "alpha note"),
                (ITEM_TYPE_OBSERVATION, 1, "beta fact"),
            ]
        )
        rows = await _count_rows(session_maker, test_project.id)
        assert {(r[0], r[1]) for r in rows} == {(ITEM_TYPE_ENTITY, 1), (ITEM_TYPE_OBSERVATION, 1)}

    @pytest.mark.asyncio
    async def test_upsert_batch_replaces_existing_vector(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "first content")])
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "completely different text")])
        rows = await _count_rows(
            session_maker, test_project.id, item_type=ITEM_TYPE_ENTITY, item_id=1
        )
        assert len(rows) == 1  # upsert, not append

    @pytest.mark.asyncio
    async def test_delete_batch_removes_only_named_keys(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "keep me"),
                (ITEM_TYPE_OBSERVATION, 2, "drop me"),
                (ITEM_TYPE_RELATION, 3, "keep relation"),
            ]
        )
        await svc.delete_batch([(ITEM_TYPE_OBSERVATION, 2)])
        rows = await _count_rows(session_maker, test_project.id)
        assert {(r[0], r[1]) for r in rows} == {(ITEM_TYPE_ENTITY, 1), (ITEM_TYPE_RELATION, 3)}

    @pytest.mark.asyncio
    async def test_clear_project_removes_all_vectors(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "a"),
                (ITEM_TYPE_OBSERVATION, 2, "b"),
                (ITEM_TYPE_RELATION, 3, "c"),
            ]
        )
        await svc.clear_project()
        assert await _count_rows(session_maker, test_project.id) == []

    @pytest.mark.asyncio
    async def test_similar_returns_typed_ranked_hits(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        # "python web" shares letters with the python entity/observation but not
        # the unrelated cooking text, so the python items rank above cooking.
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "python web framework flask"),
                (ITEM_TYPE_OBSERVATION, 2, "python web development"),
                (ITEM_TYPE_ENTITY, 3, "cooking recipes pasta baking"),
            ]
        )
        hits = await svc.similar("python web", limit=3)
        assert all(isinstance(h, EmbeddingHit) for h in hits)
        assert hits, "similar() returned no hits"
        # Scores descending, and the cooking entity is not the top hit.
        assert hits[0].score >= hits[-1].score
        assert (hits[0].item_type, hits[0].item_id) in {
            (ITEM_TYPE_ENTITY, 1),
            (ITEM_TYPE_OBSERVATION, 2),
        }
        assert (ITEM_TYPE_ENTITY, 3) not in {(h.item_type, h.item_id) for h in hits[:2]}

    @pytest.mark.asyncio
    async def test_similar_item_type_filter(self, session_maker, test_project):
        svc = await self._service(session_maker, test_project.id)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "python web framework"),
                (ITEM_TYPE_OBSERVATION, 2, "python web development"),
            ]
        )
        hits = await svc.similar("python web", limit=5, item_type=ITEM_TYPE_OBSERVATION)
        assert hits
        assert {h.item_type for h in hits} == {ITEM_TYPE_OBSERVATION}


class _CountingProvider(_FakeProvider):
    """``_FakeProvider`` that records how many times ``embed`` is called.

    Used to verify content-hash dedup (Fix 2) skips the model call for
    unchanged items and the query cache (Fix 5) skips re-embedding repeat
    queries.
    """

    def __init__(self, model_name: str = "fake/deterministic"):
        self.model_name = model_name
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return super().embed(texts)


class TestContentHashDedup:
    """Fix 2: upsert_batch skips re-embedding items whose text is unchanged."""

    async def _service(self, session_maker, project_id, provider):
        svc = EmbeddingService(session_maker, project_id, provider=provider)
        await svc.init_store()
        return svc

    @pytest.mark.asyncio
    async def test_unchanged_items_skip_embed(self, session_maker, test_project):
        provider = _CountingProvider()
        svc = await self._service(session_maker, test_project.id, provider)
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "alpha content")])
        first = provider.embed_calls
        assert first == 1
        # Same text again → no model call.
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "alpha content")])
        assert provider.embed_calls == first

    @pytest.mark.asyncio
    async def test_changed_text_reembeds(self, session_maker, test_project):
        provider = _CountingProvider()
        svc = await self._service(session_maker, test_project.id, provider)
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "alpha content")])
        before = provider.embed_calls
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "completely different text")])
        assert provider.embed_calls == before + 1

    @pytest.mark.asyncio
    async def test_model_change_reembeds_same_text(self, session_maker, test_project):
        provider_a = _CountingProvider(model_name="fake/v1")
        svc = await self._service(session_maker, test_project.id, provider_a)
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "alpha content")])
        # Swap provider model, same text → must re-embed (vector is from old model).
        provider_b = _CountingProvider(model_name="fake/v2")
        svc.provider = provider_b
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "alpha content")])
        assert provider_b.embed_calls == 1

    @pytest.mark.asyncio
    async def test_partial_batch_only_embeds_changed(self, session_maker, test_project):
        provider = _CountingProvider()
        svc = await self._service(session_maker, test_project.id, provider)
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "alpha"),
                (ITEM_TYPE_ENTITY, 2, "beta"),
                (ITEM_TYPE_OBSERVATION, 3, "gamma"),
            ]
        )
        # Only entity 2 changed; the other two are unchanged → one embed call.
        await svc.upsert_batch(
            [
                (ITEM_TYPE_ENTITY, 1, "alpha"),
                (ITEM_TYPE_ENTITY, 2, "beta CHANGED"),
                (ITEM_TYPE_OBSERVATION, 3, "gamma"),
            ]
        )
        assert provider.embed_calls == 2  # initial + one for the changed item


class TestQueryCache:
    """Fix 5: repeat queries reuse a cached query embedding."""

    @pytest.mark.asyncio
    async def test_repeat_query_skips_embed(self, session_maker, test_project):
        reset_provider_cache()  # isolate from other tests' cache state
        provider = _CountingProvider()
        svc = EmbeddingService(session_maker, test_project.id, provider=provider)
        await svc.init_store()
        await svc.upsert_batch([(ITEM_TYPE_ENTITY, 1, "python web framework")])
        await svc.similar("python web", limit=5)
        after_first = provider.embed_calls
        # Same query again → served from cache, no extra embed.
        await svc.similar("python web", limit=5)
        assert provider.embed_calls == after_first
        # A different query does embed.
        await svc.similar("rust async runtime", limit=5)
        assert provider.embed_calls == after_first + 1
        reset_provider_cache()
