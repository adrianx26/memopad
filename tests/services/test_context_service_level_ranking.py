"""Tests for level-weighted re-ranking in ContextService (Tb L0-L3 distillation).

Covers `_apply_level_weights`: when `levels_enabled` is on, each primary
entity's search score is multiplied by its configured level weight
(L3=1.0 > L2=0.85 > L1=0.70 > L0=0.40) and the list is re-sorted (ascending —
bm25's convention where a more negative score is a better match), so distilled
higher-level memories surface above raw L0 notes. The weight scales relevance;
it does not override a much stronger raw match. A uniform weight across an
all-L0 repo preserves the search-engine order, so enabling is safe even with
no derived tiers.

These tests call `_apply_level_weights` directly with hand-built ContextResultItem
lists (deterministic, no FTS dependence) and exercise the `get_by_ids` batch fetch
against the real entity_repository. One end-to-end test runs through
`build_context` with `depth=0` so the recursive-CTE path (which has an unrelated
pre-existing SQL binding issue in this environment) is not exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memopad.config import MemoPadConfig
from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import ContextResultItem, ContextService


def _config_with(**overrides) -> MemoPadConfig:
    cfg = MemoPadConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_item(entity_id: int, *, score: float = -5.0, etype: str = "entity"):
    """A minimal ContextResultItem whose primary_result duck-types as a row."""
    prim = SimpleNamespace(type=etype, id=entity_id, score=score)
    return ContextResultItem(primary_result=prim)


async def _make_entity(repo, *, permalink, title, level=None, entity_type="note"):
    now = datetime.now(timezone.utc)
    metadata = {"level": level} if level else None
    return await repo.create(
        {
            "title": title,
            "entity_type": entity_type,
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }
    )


def _ids(out):
    return [i.primary_result.id for i in out]


# --- Direct unit tests ------------------------------------------------------


@pytest.mark.asyncio
async def test_level_weights_disabled_by_default(entity_repository):
    l0 = await _make_entity(entity_repository, permalink="l0-a", title="L0a", level="L0")
    l3 = await _make_entity(entity_repository, permalink="l3-a", title="L3a", level="L3")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        # levels_enabled defaults True (flipped ON as part of native distillation),
        # so disable it explicitly to exercise the off path.
        app_config=_config_with(levels_enabled=False),
    )
    items = [_make_item(l0.id, score=-5.0), _make_item(l3.id, score=-5.0)]
    out = await svc._apply_level_weights(items)
    # No reorder / no score mutation when disabled.
    assert _ids(out) == [l0.id, l3.id]
    assert out[0].primary_result.score == -5.0
    assert out[1].primary_result.score == -5.0


@pytest.mark.asyncio
async def test_level_weight_ranks_higher_level_first(entity_repository):
    """Equal raw scores: L3 (weight 1.0) stays most negative → ranks above L0."""
    l0 = await _make_entity(entity_repository, permalink="l0-b", title="L0b", level="L0")
    l3 = await _make_entity(entity_repository, permalink="l3-b", title="L3b", level="L3")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
    )
    # l0 first in input; both raw score -5.0.
    items = [_make_item(l0.id, score=-5.0), _make_item(l3.id, score=-5.0)]
    out = await svc._apply_level_weights(items)
    # L3 weighted = -5.0*1.0 = -5.0 ; L0 weighted = -5.0*0.4 = -2.0 ; ASC → L3 first.
    assert _ids(out) == [l3.id, l0.id]
    assert out[0].primary_result.score == pytest.approx(-5.0)
    assert out[1].primary_result.score == pytest.approx(-2.0)


@pytest.mark.asyncio
async def test_level_weight_does_not_override_strong_raw_match(entity_repository):
    """L0 with a far better raw match still beats L3: weight scales, not overrides."""
    l0 = await _make_entity(entity_repository, permalink="l0-c", title="L0c", level="L0")
    l3 = await _make_entity(entity_repository, permalink="l3-c", title="L3c", level="L3")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
    )
    # L0 matches the query strongly (-10), L3 weakly (-3).
    items = [_make_item(l0.id, score=-10.0), _make_item(l3.id, score=-3.0)]
    out = await svc._apply_level_weights(items)
    # L0 weighted = -10*0.4 = -4.0 ; L3 weighted = -3*1.0 = -3.0 ; ASC → L0 first.
    assert _ids(out) == [l0.id, l3.id]


@pytest.mark.asyncio
async def test_level_weight_uniform_all_l0_preserves_order(entity_repository):
    """All-L0 with equal weight (0.40) preserves the search-engine order."""
    a = await _make_entity(entity_repository, permalink="u-a", title="UA", level="L0")
    b = await _make_entity(entity_repository, permalink="u-b", title="UB", level="L0")
    c = await _make_entity(entity_repository, permalink="u-c", title="UC", level="L0")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
    )
    # Already in search-engine (ASC) order: -9, -6, -3.
    items = [_make_item(a.id, score=-9.0), _make_item(b.id, score=-6.0), _make_item(c.id, score=-3.0)]
    out = await svc._apply_level_weights(items)
    # Uniform 0.40 scaling keeps ASC order intact — no spurious reorder.
    assert _ids(out) == [a.id, b.id, c.id]


@pytest.mark.asyncio
async def test_level_weight_mixed_levels_sort_by_weighted_score(entity_repository):
    """Four levels, equal raw scores → ordered L3, L2, L1, L0."""
    l0 = await _make_entity(entity_repository, permalink="m-l0", title="ML0", level="L0")
    l1 = await _make_entity(entity_repository, permalink="m-l1", title="ML1", level="L1")
    l2 = await _make_entity(entity_repository, permalink="m-l2", title="ML2", level="L2")
    l3 = await _make_entity(entity_repository, permalink="m-l3", title="ML3", level="L3")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
    )
    items = [_make_item(l0.id, score=-5.0), _make_item(l1.id, score=-5.0),
             _make_item(l2.id, score=-5.0), _make_item(l3.id, score=-5.0)]
    out = await svc._apply_level_weights(items)
    # weighted: L3=-5.0, L2=-4.25, L1=-3.5, L0=-2.0 → ASC = L3,L2,L1,L0
    assert _ids(out) == [l3.id, l2.id, l1.id, l0.id]


@pytest.mark.asyncio
async def test_level_weight_unlevelled_treated_as_l0(entity_repository):
    """An entity with no `level` field is weighted as L0 (the default)."""
    plain = await _make_entity(entity_repository, permalink="p-0", title="Plain")  # no level
    l3 = await _make_entity(entity_repository, permalink="p-3", title="Persona", level="L3")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
    )
    items = [_make_item(plain.id, score=-5.0), _make_item(l3.id, score=-5.0)]
    out = await svc._apply_level_weights(items)
    # plain gets L0 weight (0.40) → -2.0; L3 → -5.0 → L3 first.
    assert _ids(out) == [l3.id, plain.id]


# --- End-to-end via build_context -------------------------------------------


@pytest.mark.asyncio
async def test_level_weight_end_to_end_via_build_context(
    entity_repository, search_repository
):
    """build_context wires the level-weight re-rank in. depth=0 avoids the CTE path."""
    # Index the L0 note first, then the L3 persona. With no text query
    # (memory_url=None) the BM25 scores tie and SQLite falls back to rowid order,
    # so the natural (pre-weight) result order is note-first. The level-weight
    # re-rank must then surface the L3 persona ahead of the L0 note — so
    # `ids[0] == persona.id` actually verifies the wiring. Indexing the persona
    # first would make it first even without weighting, passing the test trivially.
    note = await _make_entity(
        entity_repository, permalink="e2e-note", title="E2ENote", level="L0"
    )
    persona = await _make_entity(
        entity_repository, permalink="e2e-persona", title="E2EPersona", level="L3"
    )

    for e in (note, persona):
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
        app_config=_config_with(levels_enabled=True),
    )
    result = await svc.build_context(
        memory_url=None, types=[SearchItemType.ENTITY], depth=0, include_observations=False
    )
    assert result.results
    ids = [r.primary_result.id for r in result.results]
    # The L3 persona must surface first despite being indexed second.
    assert ids[0] == persona.id
    assert note.id in ids


@pytest.mark.asyncio
async def test_level_weight_end_to_end_noop_when_disabled(
    entity_repository, search_repository
):
    """With levels_enabled off, build_context order is the raw search order."""
    note = await _make_entity(
        entity_repository, permalink="off-note", title="OffNote", level="L0"
    )
    persona = await _make_entity(
        entity_repository, permalink="off-persona", title="OffPersona", level="L3"
    )

    for e in (note, persona):
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
        app_config=_config_with(levels_enabled=False),  # levels_enabled defaults True
    )
    result = await svc.build_context(
        memory_url=None, types=[SearchItemType.ENTITY], depth=0, include_observations=False
    )
    assert result.results
    ids = [r.primary_result.id for r in result.results]
    # No weighting → note (indexed first, ties broken by rowid) stays first.
    assert ids[0] == note.id