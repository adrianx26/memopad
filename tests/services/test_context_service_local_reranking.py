"""Tests for the opt-in, read-only local context reranking seam."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memopad.config import MemoPadConfig
from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import ContextResultItem, ContextService
from memopad.services.local_decision_provider import (
    DecisionBatch,
    DecisionProviderResponseError,
    DecisionResponse,
    TypedDecisionAnswer,
)


class FakeDecisionProvider:
    def __init__(self, scores: list[float], *, omit_last: bool = False) -> None:
        self.scores = scores
        self.omit_last = omit_last
        self.batches: list[DecisionBatch] = []

    async def decide(self, batch: DecisionBatch) -> DecisionResponse:
        self.batches.append(batch)
        questions = batch.questions[:-1] if self.omit_last else batch.questions
        answers = {
            question.id: TypedDecisionAnswer(
                question_id=question.id,
                type="noul",
                value=self.scores[index],
            )
            for index, question in enumerate(questions)
        }
        return DecisionResponse(model="test-model", answers=answers)


def _config_with(**overrides) -> MemoPadConfig:
    config = MemoPadConfig()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


async def _create_entity(repository, permalink: str, level: str = "L1"):
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


def _item(entity, label: str) -> ContextResultItem:
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
            title=label,
            content_snippet=f"body for {label}",
        )
    )


@pytest.mark.asyncio
async def test_reranking_orders_candidates_within_same_level(entity_repository):
    entities = [await _create_entity(entity_repository, f"rerank-{index}") for index in range(3)]
    provider = FakeDecisionProvider([0.1, 0.9, 0.5])
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True),
        decision_provider=provider,
    )
    items = [_item(entity, f"candidate {index}") for index, entity in enumerate(entities)]

    reranked = await service._rerank_context_candidates(items, "query")

    assert [item.primary_result.id for item in reranked] == [
        entities[1].id,
        entities[2].id,
        entities[0].id,
    ]
    batch = provider.batches[0]
    assert isinstance(batch.state, dict)
    assert batch.state["query"] == "query"
    assert len(batch.questions) == 3


@pytest.mark.asyncio
async def test_reranking_does_not_move_candidates_across_memory_levels(entity_repository):
    l1_first = await _create_entity(entity_repository, "rerank-l1-first", "L1")
    l3 = await _create_entity(entity_repository, "rerank-l3", "L3")
    l1_last = await _create_entity(entity_repository, "rerank-l1-last", "L1")
    provider = FakeDecisionProvider([0.1, 0.99, 0.9])
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        decision_provider=provider,
    )
    items = [
        _item(l1_first, "L1 first"),
        _item(l3, "L3"),
        _item(l1_last, "L1 last"),
    ]

    reranked = await service._rerank_context_candidates(items, "query")

    assert [item.primary_result.id for item in reranked] == [
        l1_last.id,
        l3.id,
        l1_first.id,
    ]


@pytest.mark.asyncio
async def test_reranking_is_noop_without_provider_or_explicit_query(entity_repository):
    entity = await _create_entity(entity_repository, "rerank-noop")
    item = _item(entity, "candidate")
    disabled = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
    )
    provider = FakeDecisionProvider([1.0])
    enabled = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        decision_provider=provider,
    )

    assert await disabled._rerank_context_candidates([item], "query") == [item]
    assert await enabled._rerank_context_candidates([item], "query") == [item]
    assert await enabled._rerank_context_candidates([item], None) == [item]
    assert provider.batches == []


@pytest.mark.asyncio
async def test_reranking_caps_provider_batch_and_preserves_unscored_tail(entity_repository):
    entity = await _create_entity(entity_repository, "rerank-limit")
    items = [_item(entity, f"candidate {index}") for index in range(21)]
    provider = FakeDecisionProvider([index / 20 for index in range(20)])
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        decision_provider=provider,
    )

    reranked = await service._rerank_context_candidates(items, "query")

    assert len(provider.batches) == 1
    assert len(provider.batches[0].questions) == 20
    assert reranked[-1] is items[-1]
    assert reranked[0] is items[19]


@pytest.mark.asyncio
async def test_reranking_rejects_incomplete_provider_response(entity_repository):
    entities = [
        await _create_entity(entity_repository, f"rerank-invalid-{index}") for index in range(2)
    ]
    service = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        decision_provider=FakeDecisionProvider([0.5, 0.5], omit_last=True),
    )

    with pytest.raises(DecisionProviderResponseError, match="answer IDs"):
        await service._rerank_context_candidates(
            [_item(entity, f"candidate {index}") for index, entity in enumerate(entities)],
            "query",
        )


@pytest.mark.asyncio
async def test_build_context_applies_token_budget_after_reranking(
    entity_repository, search_repository
):
    entities = [
        await _create_entity(entity_repository, f"rerank-budget-{index}") for index in range(2)
    ]
    for entity in entities:
        await search_repository.index_item(
            SearchIndexRow(
                project_id=entity_repository.project_id,
                id=entity.id,
                type=SearchItemType.ENTITY.value,
                file_path=entity.file_path,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
                permalink=entity.permalink,
                title="x",
                content_snippet=None,
            )
        )
    provider = FakeDecisionProvider([0.1, 0.9])
    service = ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        app_config=_config_with(levels_enabled=True, recall_max_tokens=1),
        decision_provider=provider,
    )

    result = await service.build_context(
        types=[SearchItemType.ENTITY],
        depth=0,
        include_observations=False,
        query="find the relevant note",
    )

    assert [item.primary_result.id for item in result.results] == [entities[1].id]
