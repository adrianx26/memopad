"""Unit and integration tests for EntityAlias support."""

from textwrap import dedent

import pytest

from memopad.schemas.base import Entity as EntitySchema


@pytest.mark.asyncio
async def test_alias_stored_from_frontmatter(entity_service, entity_repository, entity_alias_repository):
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Isaac Newton",
            directory="test",
            entity_type="person",
            content=dedent("""
                ---
                aliases:
                  - Newton
                  - Sir Isaac
                ---

                # Isaac Newton
                """),
        )
    )

    aliases = await entity_alias_repository.find_by_entity(entity.id)

    assert [alias.alias for alias in aliases] == ["Newton", "Sir Isaac"]


@pytest.mark.asyncio
async def test_link_resolves_via_alias(link_resolver, entity_service):
    await entity_service.create_or_update_entity(
        EntitySchema(
            title="Isaac Newton",
            directory="test",
            entity_type="person",
            content=dedent("""
                ---
                aliases:
                  - Newton
                ---

                # Isaac Newton
                """),
        )
    )

    resolved = await link_resolver.resolve_link("Newton", strict=True)

    assert resolved is not None
    assert resolved.title == "Isaac Newton"


@pytest.mark.asyncio
async def test_alias_deleted_when_frontmatter_removed(entity_service, entity_repository, entity_alias_repository):
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Isaac Newton",
            directory="test",
            entity_type="person",
            content=dedent("""
                ---
                aliases:
                  - Newton
                ---

                # Isaac Newton
                """),
        )
    )

    assert len(await entity_alias_repository.find_by_entity(entity.id)) == 1

    updated, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Isaac Newton",
            directory="test",
            entity_type="person",
            content="# Isaac Newton",
        )
    )

    aliases = await entity_alias_repository.find_by_entity(updated.id)

    assert aliases == []


@pytest.mark.asyncio
async def test_duplicate_alias_upsert_is_idempotent(entity_service, entity_repository, entity_alias_repository):
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Isaac Newton",
            directory="test",
            entity_type="person",
            content=dedent("""
                ---
                aliases:
                  - Newton
                  - Newton
                ---

                # Isaac Newton
                """),
        )
    )

    aliases = await entity_alias_repository.find_by_entity(entity.id)

    assert [alias.alias for alias in aliases] == ["Newton"]
