"""Tests for optional per-level context token caps."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from memopad.config import MemoPadConfig
from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.memory import memory_url
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import (
    ContextResultItem,
    ContextResultRow,
    ContextService,
)
from memopad.services.provenance_service import build_drill_down_chain
from memopad.services.shortterm_context import estimate_tokens


def _config_with(**overrides) -> MemoPadConfig:
    config = MemoPadConfig()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


async def _create_entity(repository, permalink: str, level: str):
    now = datetime.now(timezone.utc)
    return await repository.create(
        {
            "title": permalink,
            "entity_type": "note",
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": {"level": level, "confidence": 0.9},
            "created_at": now,
            "updated_at": now,
        }
    )


def _item(entity, *, title: str, content: str) -> ContextResultItem:
    now = datetime.now(timezone.utc)
    return ContextResultItem(
        primary_result=SearchIndexRow(
            project_id=entity.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            file_path=entity.file_path,
            created_at=now,
            updated_at=now,
            permalink=entity.permalink,
            title=title,
            content_snippet=content,
        )
    )


def _item_tokens(item: ContextResultItem) -> int:
    primary = item.primary_result
    content = primary.content_snippet if isinstance(primary, SearchIndexRow) else primary.content
    texts = [primary.title, content]
    return sum(estimate_tokens(text) for text in texts if text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "budget_field"),
    [
        ("L0", "recall_tier_budget_l0"),
        ("L1", "recall_tier_budget_l1"),
        ("L2", "recall_tier_budget_l2"),
        ("L3", "recall_tier_budget_l3"),
    ],
)
@pytest.mark.parametrize("budget_delta", [-1, 0, 1])
async def test_each_tier_obeys_under_exact_and_over_budget_boundaries(
    entity_repository, level: str, budget_field: str, budget_delta: int
):
    entity = await _create_entity(entity_repository, f"boundary-{level}-{budget_delta}", level)
    item = _item(entity, title="title text", content="content text")
    full_cost = _item_tokens(item)
    cap = max(1, full_cost + budget_delta)
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(recall_max_tokens=0, **{budget_field: cap}),
    )

    result = await service._apply_configured_recall_budgets([item])

    assert len(result) == 1
    assert _item_tokens(result[0]) <= cap
    if budget_delta >= 0:
        assert _item_tokens(result[0]) == full_cost
    else:
        assert _item_tokens(result[0]) < full_cost


@pytest.mark.asyncio
async def test_tier_budget_handles_an_empty_candidate_list():
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=None,  # type: ignore[arg-type]
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(recall_tier_budget_l0=10),
    )

    assert await service._apply_configured_recall_budgets([]) == []


@pytest.mark.asyncio
async def test_tier_budget_prioritizes_higher_levels_and_keeps_total_hard_cap(
    entity_repository,
):
    l1 = await _create_entity(entity_repository, "budget-l1", "L1")
    l3 = await _create_entity(entity_repository, "budget-l3", "L3")
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(
            recall_max_tokens=3,
            recall_tier_budget_l1=20,
            recall_tier_budget_l3=20,
        ),
    )
    items = [
        _item(l1, title="lower", content="lower detail"),
        _item(l3, title="higher", content="higher detail"),
    ]

    result = await service._apply_configured_recall_budgets(items)

    assert [item.primary_result.id for item in result] == [l3.id]
    assert sum(_item_tokens(item) for item in result) <= 3


@pytest.mark.asyncio
async def test_tier_budget_truncates_at_exact_boundary(entity_repository):
    entity = await _create_entity(entity_repository, "budget-exact", "L2")
    item = _item(entity, title="L2 title", content="L2 content")
    exact_tokens = _item_tokens(item)
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(recall_max_tokens=0, recall_tier_budget_l2=exact_tokens),
    )

    result = await service._apply_configured_recall_budgets([item])

    assert len(result) == 1
    primary = result[0].primary_result
    assert isinstance(primary, SearchIndexRow)
    assert primary.title == "L2 title"
    assert primary.content_snippet == "L2 content"
    assert _item_tokens(result[0]) == exact_tokens


@pytest.mark.asyncio
async def test_tier_budget_counts_primary_observation_and_related_text(entity_repository):
    entity = await _create_entity(entity_repository, "budget-linked-text", "L2")
    primary = _item(entity, title="primary", content="primary content")
    now = datetime.now(timezone.utc)
    observation = ContextResultRow(
        type=SearchItemType.OBSERVATION.value,
        id=1,
        title="observation",
        permalink="",
        file_path=entity.file_path,
        depth=0,
        root_id=entity.id,
        created_at=now,
        entity_id=entity.id,
        content="observation body",
        category="fact",
    )
    related = ContextResultRow(
        type=SearchItemType.RELATION.value,
        id=2,
        title="related",
        permalink="",
        file_path=entity.file_path,
        depth=1,
        root_id=entity.id,
        created_at=now,
        relation_type="derived_from",
    )
    item = ContextResultItem(
        primary_result=primary.primary_result,
        observations=[observation],
        related_results=[related],
    )
    expected_tokens = (
        _item_tokens(item)
        + estimate_tokens(observation.title)
        + estimate_tokens(observation.content or "")
        + estimate_tokens(observation.category or "")
        + estimate_tokens(related.title)
        + estimate_tokens(related.relation_type or "")
    )
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(recall_max_tokens=0, recall_tier_budget_l2=expected_tokens),
    )

    result = await service._apply_configured_recall_budgets([item])

    assert len(result) == 1
    assert result[0].observations == [observation]
    assert result[0].related_results == [related]


@pytest.mark.asyncio
async def test_zero_tier_cap_omits_that_tier_without_blocking_other_levels(
    entity_repository,
):
    l3 = await _create_entity(entity_repository, "budget-zero-l3", "L3")
    l1 = await _create_entity(entity_repository, "budget-zero-l1", "L1")
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(
            recall_tier_budget_l3=0,
            recall_tier_budget_l1=20,
            recall_max_tokens=20,
        ),
    )

    result = await service._apply_configured_recall_budgets(
        [
            _item(l1, title="L1 fact", content="usable fact"),
            _item(l3, title="L3 persona", content="long persona content"),
        ]
    )

    assert [item.primary_result.id for item in result] == [l1.id]


@pytest.mark.asyncio
async def test_unconfigured_tiers_use_shared_budget_and_relation_uses_strongest_level(
    entity_repository,
):
    l1 = await _create_entity(entity_repository, "budget-relation-l1", "L1")
    l3 = await _create_entity(entity_repository, "budget-relation-l3", "L3")
    l2 = await _create_entity(entity_repository, "budget-unconfigured-l2", "L2")
    now = datetime.now(timezone.utc)
    relation_item = ContextResultItem(
        primary_result=ContextResultRow(
            type=SearchItemType.RELATION.value,
            id=99,
            title="derived_from",
            permalink="",
            file_path="",
            depth=0,
            root_id=l1.id,
            created_at=now,
            from_id=l1.id,
            to_id=l3.id,
            relation_type="derived_from",
        )
    )
    l2_item = _item(l2, title="L2 title", content="L2 payload")
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(recall_max_tokens=50, recall_tier_budget_l3=50),
    )

    levels = await service._context_item_levels([relation_item, l2_item])
    result = await service._apply_configured_recall_budgets([relation_item, l2_item])

    assert levels == ["L3", "L2"]
    assert [item.primary_result.id for item in result] == [99, l2.id]
    assert _item_tokens(result[1]) > 0


@pytest.mark.asyncio
async def test_build_context_enforces_tier_cap_after_distilled_filtering(
    entity_repository, search_repository
):
    l1 = await _create_entity(entity_repository, "tiered-build-l1", "L1")
    l3 = await _create_entity(entity_repository, "tiered-build-l3", "L3")
    for entity in (l1, l3):
        await search_repository.index_item(
            SearchIndexRow(
                project_id=entity_repository.project_id,
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
    service = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(
            levels_enabled=True,
            recall_max_tokens=0,
            recall_tier_budget_l3=0,
            recall_tier_budget_l1=20,
        ),
    )

    result = await service.build_context(
        types=[SearchItemType.ENTITY],
        depth=0,
        include_observations=False,
    )

    assert [item.primary_result.id for item in result.results] == [l1.id]


@pytest.mark.asyncio
async def test_direct_l0_context_remains_available_under_l0_cap(
    entity_repository, search_repository
):
    raw = await _create_entity(entity_repository, "tiered-direct-l0", "L0")
    await search_repository.index_item(
        SearchIndexRow(
            project_id=entity_repository.project_id,
            id=raw.id,
            type=SearchItemType.ENTITY.value,
            file_path=raw.file_path,
            created_at=raw.created_at,
            updated_at=raw.updated_at,
            permalink=raw.permalink,
            title=raw.title,
            content_snippet="raw source text",
        )
    )
    service = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True, recall_max_tokens=0, recall_tier_budget_l0=2),
    )

    result = await service.build_context(
        memory_url=memory_url.validate_python("memory://tiered-direct-l0"),
        depth=0,
        include_observations=False,
    )

    assert len(result.results) == 1
    assert result.results[0].primary_result.id == raw.id
    assert _item_tokens(result.results[0]) <= 2


@pytest.mark.asyncio
async def test_tier_caps_confidence_filter_and_provenance_work_together(
    entity_repository, relation_repository, search_repository
):
    raw = await _create_entity(entity_repository, "stage3-provenance-raw", "L0")
    fact = await _create_entity(entity_repository, "stage3-provenance-fact", "L1")
    persona = await _create_entity(entity_repository, "stage3-provenance-persona", "L3")
    await entity_repository.update(
        raw.id,
        {
            "entity_metadata": {
                "level": "L0",
                "confidence": 0.9,
            }
        },
    )
    await entity_repository.update(
        fact.id,
        {
            "entity_metadata": {
                "level": "L1",
                "confidence": 0.2,
                "source_entities": [f"memory://entity/{raw.permalink}"],
            }
        },
    )
    await entity_repository.update(
        persona.id,
        {
            "entity_metadata": {
                "level": "L3",
                "confidence": 0.9,
                "source_entities": [f"memory://entity/{fact.permalink}"],
            }
        },
    )

    for entity in (raw, fact, persona):
        await search_repository.index_item(
            SearchIndexRow(
                project_id=entity_repository.project_id,
                id=entity.id,
                type=SearchItemType.ENTITY.value,
                file_path=entity.file_path,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
                permalink=entity.permalink,
                title=entity.title,
                content_snippet=f"source text for {entity.permalink}",
            )
        )

    service = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(
            levels_enabled=True,
            recall_min_confidence=0.5,
            recall_max_tokens=8,
            recall_tier_budget_l3=4,
            recall_tier_budget_l1=4,
            recall_tier_budget_l0=4,
        ),
    )

    ordinary = await service.build_context(
        types=[SearchItemType.ENTITY],
        depth=0,
        include_observations=False,
    )
    assert [item.primary_result.id for item in ordinary.results] == [persona.id]
    assert _item_tokens(ordinary.results[0]) <= 4

    exact_raw = await service.build_context(
        memory_url=memory_url.validate_python(f"memory://{raw.permalink}"),
        depth=0,
        include_observations=False,
    )
    assert len(exact_raw.results) == 1
    assert exact_raw.results[0].primary_result.id == raw.id
    assert _item_tokens(exact_raw.results[0]) <= 4

    stored_persona = await entity_repository.get_by_permalink(persona.permalink)
    assert stored_persona is not None
    chain = await build_drill_down_chain(
        entity_repository,
        relation_repository,
        stored_persona,
        max_depth=5,
    )
    assert chain.children[0].permalink == fact.permalink
    assert chain.children[0].level == "L1"
    assert chain.children[0].children[0].permalink == raw.permalink
    assert chain.children[0].children[0].level == "L0"


def test_tier_budgets_reject_negative_values():
    with pytest.raises(ValidationError):
        MemoPadConfig(recall_tier_budget_l1=-1)
