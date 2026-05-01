"""Unit tests for the embedding service helpers (no model required)."""

import pytest

from memopad.services.embedding_service import (
    EmbeddingService,
    _cosine,
    _pack_vector,
    _unpack_vector,
    is_enabled,
)


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
