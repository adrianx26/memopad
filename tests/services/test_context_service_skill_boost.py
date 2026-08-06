"""Tests for the validated-skill ranking boost in ContextService (Tb G1).

Covers `_apply_skill_boost`: when `skills_enabled` is on, validated skill primary
results are moved ahead of non-skill results (stable partition preserving
search-engine order within each group). Off by default → no reordering.

These tests call `_apply_skill_boost` directly with hand-built ContextResultItem
lists (deterministic, no FTS ordering dependence) and exercise the
`get_by_ids` batch fetch against the real entity_repository. One end-to-end test
runs through `build_context` with `depth=0` so the recursive-CTE path (which has
an unrelated pre-existing SQL binding issue in this environment) is not
exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memopad.config import MemoPadConfig
from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.memory import MemoryUrl
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import ContextResultItem, ContextService
from memopad.services.skill_service import SKILL_ENTITY_TYPE, SKILL_STATUS_KEY, SKILL_VERSION_KEY, STATUS_VALIDATED


def _config_with(**overrides) -> MemoPadConfig:
    cfg = MemoPadConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_item(entity_id: int, *, etype: str = "entity"):
    """A minimal ContextResultItem whose primary_result duck-types as a result row."""
    prim = SimpleNamespace(type=etype, id=entity_id)
    return ContextResultItem(primary_result=prim)


async def _make_entity(repo, *, permalink, title, entity_type, metadata=None):
    now = datetime.now(timezone.utc)
    return await repo.create(
        {
            "title": title,
            "entity_type": entity_type,
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": metadata or None,
            "created_at": now,
            "updated_at": now,
        }
    )


@pytest.mark.asyncio
async def test_boost_disabled_by_default(entity_repository):
    skill = await _make_entity(
        entity_repository, permalink="sk-def", title="S",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    note = await _make_entity(
        entity_repository, permalink="nt-def", title="N", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=MemoPadConfig(),  # skills_enabled defaults False
    )
    items = [_make_item(note.id), _make_item(skill.id)]  # note first
    out = await svc._apply_skill_boost(items)
    # No reorder when disabled: note stays first.
    assert out[0].primary_result.id == note.id
    assert out[1].primary_result.id == skill.id


@pytest.mark.asyncio
async def test_boost_moves_validated_skill_first(entity_repository):
    skill = await _make_entity(
        entity_repository, permalink="sk-v", title="S",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 2, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    note = await _make_entity(
        entity_repository, permalink="nt-v", title="N", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(skills_enabled=True),
    )
    items = [_make_item(note.id), _make_item(skill.id)]  # note first
    out = await svc._apply_skill_boost(items)
    assert out[0].primary_result.id == skill.id  # boosted to front
    assert out[1].primary_result.id == note.id


@pytest.mark.asyncio
async def test_boost_preserves_order_within_groups(entity_repository):
    s1 = await _make_entity(
        entity_repository, permalink="sk-a", title="A",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    s2 = await _make_entity(
        entity_repository, permalink="sk-b", title="B",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    note = await _make_entity(
        entity_repository, permalink="nt-c", title="C", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(skills_enabled=True),
    )
    # Order: note, s1, s2 → skills s1,s2 move to front (stable).
    items = [_make_item(note.id), _make_item(s1.id), _make_item(s2.id)]
    out = await svc._apply_skill_boost(items)
    assert [i.primary_result.id for i in out] == [s1.id, s2.id, note.id]


@pytest.mark.asyncio
async def test_boost_no_change_when_no_validated_skills(entity_repository):
    draft_skill = await _make_entity(
        entity_repository, permalink="sk-d", title="D",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: "draft"},
    )
    note = await _make_entity(
        entity_repository, permalink="nt-d", title="N", entity_type="note"
    )
    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(skills_enabled=True),
    )
    items = [_make_item(note.id), _make_item(draft_skill.id)]
    out = await svc._apply_skill_boost(items)
    # Draft skill is not boosted — order unchanged.
    assert [i.primary_result.id for i in out] == [note.id, draft_skill.id]


@pytest.mark.asyncio
async def test_boost_end_to_end_via_build_context(
    entity_repository, search_repository
):
    """build_context wires the boost in. depth=0 avoids the CTE path."""
    from memopad.repository.search_index_row import SearchIndexRow  # noqa: F401

    # Index the NOTE first, then the skill. With no text query (memory_url=None)
    # the BM25 scores tie and SQLite falls back to rowid order, so the natural
    # (pre-boost) result order is note-first. The boost must then re-rank the
    # validated skill ahead of the note — so `ids[0] == skill.id` actually
    # verifies the boost wiring. Indexing the skill first (the old order) made
    # the skill first even without the boost, so the test passed trivially and
    # could not detect a missing/broken boost.
    note = await _make_entity(
        entity_repository, permalink="nt-e2e", title="NoteE2E", entity_type="note"
    )
    skill = await _make_entity(
        entity_repository, permalink="sk-e2e", title="SkillE2E",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )

    # Index both so build_context's primary search returns them.
    for e in (note, skill):
        await search_repository.index_item(
            SearchIndexRow(
                project_id=entity_repository.project_id,
                id=e.id,
                type=SearchItemType.ENTITY.value,
                file_path=e.file_path,
                created_at=e.created_at,
                updated_at=e.updated_at,
                permalink=e.permalink,
                title=e.title,
                content_snippet=e.title,
                content_stems=e.title,
            )
        )

    svc = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(skills_enabled=True),
    )

    result = await svc.build_context(
        memory_url=None, types=[SearchItemType.ENTITY], depth=0, include_observations=False
    )
    assert result.results
    ids = [r.primary_result.id for r in result.results]
    # The validated skill must be first.
    assert ids[0] == skill.id
    assert note.id in ids