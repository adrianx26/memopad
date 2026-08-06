"""Tests for the retrieval budget (Tb G4): per-memory cap + graceful timeout.

These cover the G4 additions to ContextService in isolation:
- `_truncate_memory`: caps a single memory item's content when the configured
  limit is set, with a visible truncation marker; no-op when disabled (default).
- `_with_recall_timeout`: wraps a retrieval stage in a hard timeout and degrades
  gracefully (returns None) instead of failing the conversation; no-op when the
  timeout is 0 (default).
- integration: a long observation is truncated in build_context output when the
  cap is enabled, and untouched when disabled.

The integration test uses a single entity with NO relations so it does not
exercise the recursive-CTE path (which has an unrelated pre-existing SQL binding
issue in this environment; see `tb-borrow-progress.md`).
"""

import asyncio

import pytest

from memopad.config import MemoPadConfig
from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.memory import MemoryUrl
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import ContextService


def _config_with(**overrides) -> MemoPadConfig:
    cfg = MemoPadConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


async def _index_entity(search_repository, entity, project_id: int) -> None:
    """Index an entity into the FTS search_index so build_context can find it.

    build_context resolves primary results through search_repository.search, which
    reads the search_index table — a direct entity_repository.create does NOT populate
    it. Mirrors the setup in tests/services/test_context_service.py.
    """
    await search_repository.index_item(
        SearchIndexRow(
            project_id=project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            file_path=entity.file_path,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            permalink=entity.permalink,
            title=entity.title,
            content_snippet=entity.title,
        )
    )


@pytest.mark.asyncio
async def test_truncate_memory_disabled_by_default():
    # Default config: recall_max_chars_per_memory == 0 -> no truncation.
    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=MemoPadConfig(),
    )
    long_text = "x" * 5000
    assert svc._truncate_memory(long_text) == long_text
    assert svc._truncate_memory(None) is None


@pytest.mark.asyncio
async def test_truncate_memory_caps_when_enabled():
    cfg = _config_with(recall_max_chars_per_memory=100)
    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=cfg,
    )
    long_text = "y" * 5000
    out = svc._truncate_memory(long_text)
    assert out is not None
    assert out.endswith("…[truncated]")
    # Body is capped to the limit (marker appended after).
    assert len(out) <= 100 + len(" …[truncated]")

    # Short text is untouched.
    short = "short"
    assert svc._truncate_memory(short) == short


@pytest.mark.asyncio
async def test_with_recall_timeout_passes_result_when_fast():
    cfg = _config_with(recall_timeout_ms=2000)

    # ContextService.__init__ needs the timeout configured; build a minimal instance.
    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=cfg,
    )

    async def quick():
        return 42

    assert await svc._with_recall_timeout(quick(), "quick_stage") == 42


@pytest.mark.asyncio
async def test_with_recall_timeout_degrades_gracefully_on_timeout():
    cfg = _config_with(recall_timeout_ms=50)  # 0.05s

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=cfg,
    )

    async def slow():
        await asyncio.sleep(1.0)
        return "should-not-happen"

    # On timeout the stage returns None instead of raising — graceful degradation.
    result = await svc._with_recall_timeout(slow(), "slow_stage")
    assert result is None


@pytest.mark.asyncio
async def test_with_recall_timeout_noop_when_disabled():
    cfg = _config_with(recall_timeout_ms=0)
    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=cfg,
    )

    async def coro():
        return "ok"

    # When disabled, the coro runs directly (no asyncio.wait_for wrapping).
    assert await svc._with_recall_timeout(coro(), "stage") == "ok"


@pytest.mark.asyncio
async def test_build_context_truncates_long_observation_when_enabled(
    entity_repository, observation_repository, search_repository
):
    """Integration: a long observation is capped in build_context output.

    Uses a single entity with no relations to avoid the unrelated CTE binding bug.
    """
    from datetime import datetime, timezone

    from memopad.models.knowledge import Entity, Observation

    now = datetime.now(timezone.utc)
    entity = await entity_repository.create(
        {
            "title": "LongNote",
            "entity_type": "note",
            "permalink": "longnote-budget",
            "file_path": "longnote-budget.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    long_body = "Z" * 4000
    await observation_repository.create(
        {
            "entity_id": entity.id,
            "content": long_body,
            "category": "note",
        }
    )
    await _index_entity(search_repository, entity, entity.project_id)

    cfg = _config_with(recall_max_chars_per_memory=120)
    svc = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=cfg,
    )

    memory_url = MemoryUrl(f"memory://longnote-budget")
    result = await svc.build_context(memory_url=memory_url, depth=1, include_observations=True)

    assert result.results, "expected at least one primary result"
    primary = result.results[0]
    assert primary.observations, "expected the observation to be fetched"
    obs_content = primary.observations[0].content
    assert obs_content is not None
    assert obs_content.endswith("…[truncated]")
    assert len(obs_content) <= 120 + len(" …[truncated]")


@pytest.mark.asyncio
async def test_build_context_does_not_truncate_when_disabled(
    entity_repository, observation_repository, search_repository
):
    """Default config (cap disabled) leaves observation content intact."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    entity = await entity_repository.create(
        {
            "title": "LongNote2",
            "entity_type": "note",
            "permalink": "longnote-budget2",
            "file_path": "longnote-budget2.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    long_body = "Q" * 4000
    await observation_repository.create(
        {"entity_id": entity.id, "content": long_body, "category": "note"}
    )
    await _index_entity(search_repository, entity, entity.project_id)

    # Default MemoPadConfig -> recall_max_chars_per_memory == 0 (disabled).
    svc = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=MemoPadConfig(),
    )

    memory_url = MemoryUrl(f"memory://longnote-budget2")
    result = await svc.build_context(memory_url=memory_url, depth=1, include_observations=True)

    assert result.results
    obs_content = result.results[0].observations[0].content
    assert obs_content == long_body  # untouched