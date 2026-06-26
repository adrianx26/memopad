"""Unit tests for ConflictService.

Tests cover:
- No conflict when observations are in different categories
- Conflict detected when same entity + same category + different content
- No conflict when observations are identical (same content)
- Conflict resolution clears both sides of the pair
- Similarity ratio helper behaves correctly
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db as db_module
from memopad.models.knowledge import Observation
from memopad.services.conflict_service import (
    ConflictService,
    _similarity_ratio,
)


# --- Unit tests for similarity helper ---


def test_similarity_identical():
    assert _similarity_ratio("active", "active") == 1.0


def test_similarity_case_insensitive():
    # Normalised to lowercase before comparison
    assert _similarity_ratio("Active", "active") == 1.0


def test_similarity_whitespace_normalised():
    assert _similarity_ratio("project  active", "project active") == 1.0


def test_similarity_completely_different():
    score = _similarity_ratio("active", "deprecated")
    assert score <= 0.5  # little character overlap


def test_similarity_partially_overlapping():
    score = _similarity_ratio("the project is active", "the project is deprecated")
    # shares "the project is" — should be partially similar
    assert 0.3 < score < 0.9


# --- Fixtures ---


def _make_obs(
    id_: int,
    entity_id: int,
    category: str,
    content: str,
    conflict_score=None,
    conflicting_obs_id=None,
    conflict_resolved=False,
) -> Observation:
    obs = MagicMock(spec=Observation)
    obs.id = id_
    obs.entity_id = entity_id
    obs.category = category
    obs.content = content
    obs.conflict_score = conflict_score
    obs.conflicting_obs_id = conflicting_obs_id
    obs.conflict_resolved = conflict_resolved
    return obs


def _make_service() -> tuple[ConflictService, MagicMock]:
    repo = MagicMock()
    repo.session_maker = MagicMock(spec=async_sessionmaker)
    repo.project_id = 1
    service = ConflictService(repo)
    return service, repo


# --- Test: no conflict across different categories ---


@pytest.mark.asyncio
async def test_no_conflict_different_category():
    service, repo = _make_service()

    obs_a = _make_obs(1, 10, "status", "active")
    obs_b = _make_obs(2, 10, "priority", "high")

    # find_by_entity returns both observations
    repo.find_by_entity = AsyncMock(return_value=[obs_a, obs_b])

    with patch.object(service, "_write_conflicts", new=AsyncMock()) as mock_write:
        results = await service.detect_and_mark(entity_id=10, new_observations=[obs_a, obs_b])

    assert results == []
    mock_write.assert_not_called()


# --- Test: conflict detected (same category, different content) ---


@pytest.mark.asyncio
async def test_conflict_same_category_different_content():
    service, repo = _make_service()

    obs_a = _make_obs(1, 10, "status", "active and running smoothly")
    obs_b = _make_obs(2, 10, "status", "deprecated and scheduled for removal")

    repo.find_by_entity = AsyncMock(return_value=[obs_a, obs_b])

    with patch.object(service, "_write_conflicts", new=AsyncMock()) as mock_write:
        results = await service.detect_and_mark(entity_id=10, new_observations=[obs_a, obs_b])

    assert len(results) == 1
    result = results[0]
    assert result.obs_a_id == 1
    assert result.obs_b_id == 2
    assert result.score > 0.15  # above threshold
    mock_write.assert_called_once()


# --- Test: no conflict when content is identical ---


@pytest.mark.asyncio
async def test_no_conflict_same_category_same_content():
    service, repo = _make_service()

    obs_a = _make_obs(1, 10, "status", "active")
    obs_b = _make_obs(2, 10, "status", "active")

    repo.find_by_entity = AsyncMock(return_value=[obs_a, obs_b])

    with patch.object(service, "_write_conflicts", new=AsyncMock()) as mock_write:
        results = await service.detect_and_mark(entity_id=10, new_observations=[obs_a, obs_b])

    assert results == []
    mock_write.assert_not_called()


# --- Test: skip already-resolved observations ---


@pytest.mark.asyncio
async def test_already_resolved_observations_skipped():
    service, repo = _make_service()

    obs_a = _make_obs(1, 10, "status", "active", conflict_resolved=True)
    obs_b = _make_obs(2, 10, "status", "deprecated")

    repo.find_by_entity = AsyncMock(return_value=[obs_a, obs_b])

    with patch.object(service, "_write_conflicts", new=AsyncMock()) as mock_write:
        results = await service.detect_and_mark(entity_id=10, new_observations=[obs_a, obs_b])

    assert results == []
    mock_write.assert_not_called()


# --- Test: skip already-flagged pairs ---


@pytest.mark.asyncio
async def test_already_flagged_pair_skipped():
    service, repo = _make_service()

    # obs_a already knows its conflict is obs_b
    obs_a = _make_obs(1, 10, "status", "active", conflicting_obs_id=2)
    obs_b = _make_obs(2, 10, "status", "deprecated")

    repo.find_by_entity = AsyncMock(return_value=[obs_a, obs_b])

    with patch.object(service, "_write_conflicts", new=AsyncMock()) as mock_write:
        results = await service.detect_and_mark(entity_id=10, new_observations=[obs_a, obs_b])

    assert results == []
    mock_write.assert_not_called()


# --- Test: empty observations list returns immediately ---


@pytest.mark.asyncio
async def test_empty_observations_no_op():
    service, repo = _make_service()
    repo.find_by_entity = AsyncMock(return_value=[])

    results = await service.detect_and_mark(entity_id=10, new_observations=[])

    assert results == []
    repo.find_by_entity.assert_not_called()


# --- Test: batched conflict writes issue a single UPDATE ---


@pytest.mark.asyncio
async def test_write_conflicts_batches_into_single_update():
    """_write_conflicts must emit ONE execute() (a batched CASE UPDATE), not 2N.

    Regression guard for the 4.4 batching: previously it issued two UPDATEs per
    conflict pair (one per side). Now one statement covers every flagged row.
    """
    from memopad.services.conflict_service import ConflictResult

    service, repo = _make_service()

    captured = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            captured.append(stmt)

    fake_session = _FakeSession()
    with patch.object(
        db_module, "scoped_session", return_value=fake_session
    ), patch.object(service.observation_repository, "session_maker", fake_session):
        results = [
            ConflictResult(obs_a_id=1, obs_b_id=2, score=0.9),
            ConflictResult(obs_a_id=3, obs_b_id=4, score=0.8),
        ]
        await service._write_conflicts(results, provenance_path="notes/x.md")

    # Exactly one batched UPDATE — not one per side per pair.
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_write_conflicts_empty_is_noop():
    service, repo = _make_service()

    captured = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            captured.append(stmt)

    fake_session = _FakeSession()
    with patch.object(
        db_module, "scoped_session", return_value=fake_session
    ) as mock_scoped:
        await service._write_conflicts([], provenance_path=None)

        assert captured == []
        mock_scoped.assert_not_called()
