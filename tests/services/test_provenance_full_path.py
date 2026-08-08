"""Full-path provenance enforcement tests (Tb G5).

`validate_provenance` is unit-tested in test_provenance_service.py as a pure
function. These tests exercise the *integration* path: that EntityService's full
`create_entity` / `update_entity` routes call the guard on the parsed
frontmatter, so the L1/L2/L3-must-carry-source_entities invariant holds
uniformly across every write path (not just the fast paths).

The default `app_config` fixture has `levels_enabled=True` (flipped ON as part of
the native distillation work), so the on-path tests run with the guard active.
The off-path test constructs an EntityService with `levels_enabled=False` to
confirm ordinary note creation is completely untouched when the feature is off.
"""

import pytest

from memopad.schemas import Entity as EntitySchema
from memopad.services.entity_service import EntityService
from memopad.services.provenance_service import ProvenanceError


def _l1_schema(*, title="L1 No Source", sources=None):
    metadata = {"level": "L1"}
    if sources is not None:
        metadata["source_entities"] = sources
    return EntitySchema(
        title=title,
        directory="test",
        entity_type="note",
        entity_metadata=metadata,
        content="# L1 fact\n- [fact] derived content\n",
    )


@pytest.mark.asyncio
async def test_create_entity_rejects_l1_without_sources(entity_service):
    """Full create path refuses an L1 memory missing source_entities."""
    with pytest.raises(ProvenanceError, match="source_entities"):
        await entity_service.create_entity(_l1_schema())


@pytest.mark.asyncio
async def test_create_entity_accepts_l1_with_sources(entity_service):
    """Full create path persists an L1 memory that declares provenance."""
    schema = _l1_schema(
        title="L1 With Source", sources=["memory://entity/some-l0"]
    )
    entity = await entity_service.create_entity(schema)
    assert entity.entity_metadata.get("level") == "L1"
    assert entity.entity_metadata.get("source_entities") == [
        "memory://entity/some-l0"
    ]


@pytest.mark.asyncio
async def test_create_entity_allows_l0_no_metadata(entity_service):
    """An ordinary note with no `level` field is untouched by the guard."""
    schema = EntitySchema(
        title="Plain Note",
        directory="test",
        entity_type="note",
        content="# Plain\n- [note] nothing to see here\n",
    )
    entity = await entity_service.create_entity(schema)
    assert entity.title == "Plain Note"
    # No level → defaults to unlevelled (L0), no provenance required.
    assert entity.entity_metadata.get("level") is None


@pytest.mark.asyncio
async def test_update_entity_rejects_l1_without_sources(entity_service):
    """Updating an L0 note into an L1 memory without sources is refused."""
    # Start as a plain L0 note.
    l0 = await entity_service.create_entity(
        EntitySchema(
            title="L0 To Promote",
            directory="test",
            entity_type="note",
            content="# L0\n- [note] raw note\n",
        )
    )
    # Re-fetch the persisted model so update_entity has a fresh entity handle.
    fresh = await entity_service.repository.get_by_permalink(l0.permalink)

    # Attempt to promote it to L1 without provenance.
    schema = EntitySchema(
        title="L0 To Promote",
        directory="test",
        entity_type="note",
        entity_metadata={"level": "L1"},
        content="# L0\n- [fact] promoted but no provenance\n",
    )
    with pytest.raises(ProvenanceError, match="source_entities"):
        await entity_service.update_entity(fresh, schema)


@pytest.mark.asyncio
async def test_update_entity_accepts_l1_with_sources(entity_service):
    """Updating into an L1 memory that declares provenance succeeds."""
    l0 = await entity_service.create_entity(
        EntitySchema(
            title="L0 Promote Good",
            directory="test",
            entity_type="note",
            content="# L0\n- [note] raw note\n",
        )
    )
    fresh = await entity_service.repository.get_by_permalink(l0.permalink)

    schema = EntitySchema(
        title="L0 Promote Good",
        directory="test",
        entity_type="note",
        entity_metadata={
            "level": "L1",
            "source_entities": ["memory://entity/" + l0.permalink],
        },
        content="# L0\n- [fact] promoted with provenance\n",
    )
    updated = await entity_service.update_entity(fresh, schema)
    assert updated.entity_metadata.get("level") == "L1"


@pytest.mark.asyncio
async def test_create_entity_noop_when_levels_disabled(
    entity_repository,
    observation_repository,
    relation_repository,
    entity_parser,
    file_service,
    link_resolver,
    app_config,
):
    """With levels_enabled off, an L1 memory without sources is allowed (no-op)."""
    disabled_config = app_config.model_copy(update={"levels_enabled": False})
    service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=disabled_config,
    )
    # No raise — the guard is a no-op when the flag is off.
    entity = await service.create_entity(_l1_schema(title="L1 Disabled Flag"))
    assert entity.entity_metadata.get("level") == "L1"