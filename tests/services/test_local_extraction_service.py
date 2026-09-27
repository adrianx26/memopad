"""Tests for local relation and tag suggestions."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.search import SearchItemType
from memopad.services.local_decision_provider import (
    DecisionResponse,
    TypedDecisionAnswer,
)
from memopad.services.local_extraction_service import LocalExtractionService


def _row(
    entity_id: int,
    title: str,
    *,
    permalink: str,
    tags: list[str] | None = None,
) -> SearchIndexRow:
    now = datetime.now()
    return SearchIndexRow(
        project_id=1,
        id=entity_id,
        type=SearchItemType.ENTITY.value,
        file_path=f"{permalink}.md",
        created_at=now,
        updated_at=now,
        title=title,
        permalink=permalink,
        metadata={"tags": tags or []},
    )


@pytest.mark.asyncio
async def test_suggest_uses_explicit_aliases_and_related_note_tags():
    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                _row(
                    1,
                    "System Architecture",
                    permalink="architecture",
                    tags=["design", "backend"],
                ),
                _row(
                    2,
                    "Backend Service",
                    permalink="backend-service",
                    tags=["backend", "python"],
                ),
                _row(99, "Current Note", permalink="current"),
            ]
        )
    )
    aliases = SimpleNamespace(
        find_by_entities=AsyncMock(
            return_value=[SimpleNamespace(entity_id=1, alias="System design")]
        )
    )
    service = LocalExtractionService(search, aliases)

    result = await service.suggest(
        "System design supports backend architecture.", source_entity_id=99
    )

    assert result["relations"] == [
        {
            "title": "System Architecture",
            "permalink": "architecture",
            "matched_text": "System design",
            "confidence": 0.75,
        }
    ]
    assert result["tags"][0] == {
        "tag": "backend",
        "supporting_entities": 2,
        "confidence": 1.0,
    }
    search.search.assert_awaited_once()
    query = search.search.await_args.args[0]
    assert query.entity_types == [SearchItemType.ENTITY]
    aliases.find_by_entities.assert_awaited_once_with([1, 2])


@pytest.mark.asyncio
async def test_suggest_returns_empty_for_content_without_search_terms():
    search = SimpleNamespace(search=AsyncMock())
    aliases = SimpleNamespace(find_by_entities=AsyncMock())
    service = LocalExtractionService(search, aliases)

    result = await service.suggest("a, I, 42", source_entity_id=1)

    assert result == {"relations": [], "tags": []}
    search.search.assert_not_awaited()
    aliases.find_by_entities.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_without_candidates_returns_empty_suggestions():
    search = SimpleNamespace(search=AsyncMock(return_value=[]))
    aliases = SimpleNamespace(find_by_entities=AsyncMock(return_value=[]))
    service = LocalExtractionService(search, aliases)

    result = await service.suggest("Architecture patterns use modules.", source_entity_id=1)

    assert result == {"relations": [], "tags": []}
    aliases.find_by_entities.assert_awaited_once_with([])


@pytest.mark.asyncio
async def test_decide_relation_only_selects_supplied_candidates_and_bounds_context():
    class FakeDecisionProvider:
        batch = None

        async def decide(self, batch):
            self.batch = batch
            return DecisionResponse(
                model="local-test-model",
                answers={
                    "relation_target": TypedDecisionAnswer(
                        question_id="relation_target",
                        type="choice",
                        value="candidate_1",
                        probabilities={"candidate_0": 0.1, "candidate_1": 0.8, "none": 0.1},
                    )
                },
            )

    provider = FakeDecisionProvider()
    service = LocalExtractionService(SimpleNamespace(), SimpleNamespace(), provider)
    content = "x" * 500 + " MemoPad links System Architecture to Backend Service. " + "y" * 500
    candidates = [
        {"title": "System Architecture", "permalink": "architecture"},
        {"title": "Backend Service", "permalink": "backend-service"},
    ]

    result = await service._decide_relation(
        content,
        mention="Backend Service",
        candidates=candidates,
    )

    assert result == {
        "candidate": candidates[1],
        "model": "local-test-model",
        "selected_probability": 0.8,
    }
    assert provider.batch is not None
    assert len(provider.batch.state) < len(content)
    assert "Mention: Backend Service" in provider.batch.state
    question = provider.batch.questions[0]
    assert list(question.criteria) == ["candidate_0", "candidate_1", "none"]
    assert "backend-service" in question.criteria["candidate_1"]


@pytest.mark.asyncio
async def test_decide_relation_abstention_returns_none():
    class FakeDecisionProvider:
        async def decide(self, batch):
            return DecisionResponse(
                model="local-test-model",
                answers={
                    "relation_target": TypedDecisionAnswer(
                        question_id="relation_target",
                        type="choice",
                        value="none",
                    )
                },
            )

    service = LocalExtractionService(SimpleNamespace(), SimpleNamespace(), FakeDecisionProvider())
    result = await service._decide_relation(
        "The design mentions an unknown service.",
        mention="unknown service",
        candidates=[{"title": "Known Service", "permalink": "known-service"}],
    )

    assert result is None


@pytest.mark.asyncio
async def test_decide_relation_rejects_unbounded_or_invalid_candidates():
    service = LocalExtractionService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(decide=AsyncMock()),
    )
    with pytest.raises(ValueError, match="must be a non-empty phrase"):
        await service._decide_relation(
            "No target appears here.",
            mention="missing target",
            candidates=[{"title": "Target", "permalink": "target"}],
        )
    with pytest.raises(ValueError, match="must have a non-empty permalink"):
        await service._decide_relation(
            "The target appears here.",
            mention="target",
            candidates=[{"title": "Target", "permalink": ""}],
        )


@pytest.mark.asyncio
async def test_decide_relation_requires_provider_and_skips_empty_candidate_set():
    service = LocalExtractionService(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(RuntimeError, match="provider is required"):
        await service._decide_relation(
            "A target appears.",
            mention="target",
            candidates=[{"title": "Target", "permalink": "target"}],
        )

    provider = SimpleNamespace(decide=AsyncMock())
    service = LocalExtractionService(SimpleNamespace(), SimpleNamespace(), provider)
    result = await service._decide_relation(
        "A target appears.",
        mention="target",
        candidates=[],
    )
    assert result is None
    provider.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_relation_target_uses_only_local_candidate_retrieval():
    class FakeDecisionProvider:
        batch = None

        async def decide(self, batch):
            self.batch = batch
            return DecisionResponse(
                model="local-test-model",
                answers={
                    "relation_target": TypedDecisionAnswer(
                        question_id="relation_target",
                        type="choice",
                        value="candidate_0",
                    )
                },
            )

    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                _row(1, "System Architecture", permalink="architecture"),
                _row(99, "Current Note", permalink="current"),
                _row(2, "Unmentioned Candidate", permalink="unmentioned"),
            ]
        )
    )
    aliases = SimpleNamespace(
        find_by_entities=AsyncMock(
            return_value=[SimpleNamespace(entity_id=1, alias="System design")]
        )
    )
    provider = FakeDecisionProvider()
    service = LocalExtractionService(search, aliases, provider)

    result = await service.suggest_relation_target(
        "System design supports the architecture.",
        mention="System design",
        source_entity_id=99,
    )

    assert result is not None
    assert result["candidate"]["permalink"] == "architecture"
    criteria = provider.batch.questions[0].criteria
    assert list(criteria) == ["candidate_0", "none"]
    assert "unmentioned" not in str(criteria)
