"""Tests for hub-aware context scoring."""

from datetime import datetime, timezone

import pytest

from memopad.models.knowledge import Entity, Relation
from memopad.services.context_service import ContextResultRow


@pytest.mark.asyncio
async def test_hub_node_ranked_lower(context_service, relation_repository, entity_repository):
    hub, leaf = await _create_entities(entity_repository)
    await _create_relations(relation_repository, entity_repository, hub.id, 50)
    await _create_relations(relation_repository, entity_repository, leaf.id, 2)

    rows = [
        ContextResultRow(
            type="entity",
            id=hub.id,
            title=hub.title,
            permalink=hub.permalink,
            file_path=hub.file_path,
            depth=1,
            root_id=hub.id,
            created_at=datetime.now(timezone.utc),
        ),
        ContextResultRow(
            type="entity",
            id=leaf.id,
            title=leaf.title,
            permalink=leaf.permalink,
            file_path=leaf.file_path,
            depth=2,
            root_id=hub.id,
            created_at=datetime.now(timezone.utc),
        ),
    ]

    degrees = await context_service._fetch_entity_degrees([hub.id, leaf.id])
    ranked = context_service._apply_hub_penalty(rows, degrees)

    assert ranked[0].id == leaf.id
    assert ranked[0].relevance_score > ranked[1].relevance_score


@pytest.mark.asyncio
async def test_leaf_node_ranked_higher_than_shallow_hub(context_service, relation_repository, entity_repository):
    hub, leaf = await _create_entities(entity_repository)
    await _create_relations(relation_repository, entity_repository, hub.id, 50)
    await _create_relations(relation_repository, entity_repository, leaf.id, 2)

    rows = [
        ContextResultRow(
            type="entity",
            id=hub.id,
            title=hub.title,
            permalink=hub.permalink,
            file_path=hub.file_path,
            depth=1,
            root_id=hub.id,
            created_at=datetime.now(timezone.utc),
        ),
        ContextResultRow(
            type="entity",
            id=leaf.id,
            title=leaf.title,
            permalink=leaf.permalink,
            file_path=leaf.file_path,
            depth=2,
            root_id=hub.id,
            created_at=datetime.now(timezone.utc),
        ),
    ]

    degrees = await context_service._fetch_entity_degrees([hub.id, leaf.id])
    ranked = context_service._apply_hub_penalty(rows, degrees)

    assert ranked[0].id == leaf.id


@pytest.mark.asyncio
async def test_no_entities_no_crash(context_service):
    assert await context_service._fetch_entity_degrees([]) == {}


@pytest.mark.asyncio
async def test_degree_query_counts_incoming_and_outgoing(context_service, relation_repository, entity_repository):
    hub, leaf, other = await _create_entities(entity_repository)
    await relation_repository.add_all(
        [
            Relation(
                project_id=relation_repository.project_id,
                from_id=hub.id,
                to_id=leaf.id,
                to_name=leaf.title,
                relation_type="points_to",
            ),
            Relation(
                project_id=relation_repository.project_id,
                from_id=other.id,
                to_id=hub.id,
                to_name=hub.title,
                relation_type="points_to",
            ),
        ]
    )

    degrees = await context_service._fetch_entity_degrees([hub.id, leaf.id, other.id])

    assert degrees[hub.id] == 2
    assert degrees[leaf.id] == 1
    assert degrees[other.id] == 1


async def _create_entities(entity_repository):
    now = datetime.now(timezone.utc)
    hub = await entity_repository.create(
        {
            "title": "Hub",
            "entity_type": "test",
            "permalink": "hub",
            "file_path": "hub.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    leaf = await entity_repository.create(
        {
            "title": "Leaf",
            "entity_type": "test",
            "permalink": "leaf",
            "file_path": "leaf.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    other = await entity_repository.create(
        {
            "title": "Other",
            "entity_type": "test",
            "permalink": "other",
            "file_path": "other.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    return hub, leaf, other


async def _create_relations(relation_repository, entity_repository, from_id: int, count: int):
    relations = []
    for index in range(count):
        target = await entity_repository.create(
            {
                "title": f"Target {index}",
                "entity_type": "test",
                "permalink": f"target-{from_id}-{index}",
                "file_path": f"target-{from_id}-{index}.md",
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        relations.append(
            Relation(
                project_id=relation_repository.project_id,
                from_id=from_id,
                to_id=target.id,
                to_name=target.title,
                relation_type="connects_to",
            )
        )
    await relation_repository.add_all(relations)
