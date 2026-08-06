"""Tests for provenance_service — reversible distillation chain (Tb G5).

Covers:
- the fail-fast provenance invariant (validate_provenance) — derived levels must
  carry non-empty source_entities; L0 and unlevelled entities are exempt; the
  check is a no-op unless `levels_enabled` is set.
- source parsing helpers (parse_source_entities, entity_level).
- build_drill_down_chain traversal against real repositories (SQLite via the
  session_maker fixture), following frontmatter `source_entities` down to L0,
  marking unresolved sources, and the `derived_from` relation backref path.
"""

from datetime import datetime, timezone

import pytest

from memopad.services.provenance_service import (
    DERIVED_FROM_RELATION_TYPE,
    LEVEL_L0,
    ProvenanceError,
    build_drill_down_chain,
    entity_level,
    parse_source_entities,
    render_drill_down_chain,
    validate_provenance,
)


# --- Pure-function tests (no DB) -------------------------------------------


class TestValidateProvenance:
    def test_noop_when_levels_disabled(self):
        # Even a derived entity with no sources must pass when the flag is off,
        # so existing note creation is completely untouched.
        validate_provenance({"level": "L1"}, levels_enabled=False)
        validate_provenance(None, levels_enabled=False)

    def test_noop_for_l0_and_unlevelled(self):
        validate_provenance({"level": "L0"}, levels_enabled=True)
        validate_provenance({}, levels_enabled=True)
        validate_provenance({"level": "weird"}, levels_enabled=True)

    @pytest.mark.parametrize("level", ["L1", "L2", "L3"])
    def test_derived_level_without_sources_raises(self, level):
        with pytest.raises(ProvenanceError, match="source_entities"):
            validate_provenance({"level": level}, levels_enabled=True)

    @pytest.mark.parametrize("level", ["L1", "L2", "L3"])
    def test_derived_level_with_empty_sources_raises(self, level):
        with pytest.raises(ProvenanceError):
            validate_provenance(
                {"level": level, "source_entities": []}, levels_enabled=True
            )

    def test_derived_level_with_sources_passes(self):
        validate_provenance(
            {"level": "L2", "source_entities": ["memory://entity/foo"]},
            levels_enabled=True,
        )
        # A single string source (not a list) also passes.
        validate_provenance(
            {"level": "L3", "source_entities": "memory://entity/foo"},
            levels_enabled=True,
        )


class TestSourceParsing:
    def test_strip_memory_prefixes(self):
        assert parse_source_entities(
            {"source_entities": ["memory://entity/foo-bar", "baz"]}
        ) == ["foo-bar", "baz"]

    def test_single_string_source(self):
        assert parse_source_entities(
            {"source_entities": "memory://entity/x"}
        ) == ["x"]

    def test_missing_or_empty(self):
        assert parse_source_entities(None) == []
        assert parse_source_entities({}) == []
        assert parse_source_entities({"source_entities": []}) == []

    def test_entity_level_defaults_to_l0(self):
        assert entity_level(None) == LEVEL_L0
        assert entity_level({}) == LEVEL_L0
        assert entity_level({"level": "L2"}) == "L2"
        assert entity_level({"level": "bogus"}) == LEVEL_L0


# --- Integration tests (real repositories via fixtures) -------------------


def _now():
    return datetime.now(timezone.utc)


async def _make_entity(repo, *, permalink, title, level=None, sources=None, entity_type="note"):
    metadata = {}
    if level:
        metadata["level"] = level
    if sources:
        metadata["source_entities"] = sources
    return await repo.create(
        {
            "title": title,
            "entity_type": entity_type,
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": metadata or None,
            "created_at": _now(),
            "updated_at": _now(),
        }
    )


@pytest.mark.asyncio
async def test_drill_down_follows_source_entities_to_l0(
    entity_repository, relation_repository
):
    """L3 -> L1 -> L0 chain resolved through frontmatter source_entities."""
    raw = await _make_entity(
        entity_repository, permalink="book-ch3", title="Raw Book Ch3", level="L0"
    )
    fact = await _make_entity(
        entity_repository,
        permalink="fact-ohms-law",
        title="Ohms Law",
        level="L1",
        sources=["memory://entity/book-ch3"],
    )
    persona = await _make_entity(
        entity_repository,
        permalink="persona-main",
        title="Main Persona",
        level="L3",
        sources=["memory://entity/fact-ohms-law"],
    )

    root = await build_drill_down_chain(
        entity_repository, relation_repository, persona, target_level="L0", max_depth=5
    )
    rendered = render_drill_down_chain(root)

    # Chain reaches L0 ground truth through the intermediate L1.
    assert "[L3]" in rendered and "persona-main" in rendered
    assert "[L1]" in rendered and "fact-ohms-law" in rendered
    assert "[L0]" in rendered and "book-ch3" in rendered
    assert root.level == "L3"
    assert root.children[0].level == "L1"
    assert root.children[0].children[0].level == "L0"


@pytest.mark.asyncio
async def test_drill_down_marks_unresolved_sources(
    entity_repository, relation_repository
):
    """A source reference that cannot be resolved becomes an unresolved leaf."""
    fact = await _make_entity(
        entity_repository,
        permalink="fact-bad",
        title="Bad Fact",
        level="L1",
        sources=["memory://entity/does-not-exist"],
    )
    root = await build_drill_down_chain(
        entity_repository, relation_repository, fact, target_level="L0", max_depth=5
    )
    rendered = render_drill_down_chain(root)

    assert "_[unresolved]_" in rendered
    assert root.children[0].resolved is False


@pytest.mark.asyncio
async def test_drill_down_uses_derived_from_relations(
    entity_repository, relation_repository
):
    """When frontmatter source_entities is absent, derived_from relations are followed."""
    raw = await _make_entity(
        entity_repository, permalink="raw-src", title="Raw Source", level="L0"
    )
    fact = await _make_entity(
        entity_repository, permalink="fact-rel", title="Fact From Rel", level="L1"
    )
    # No frontmatter source_entities; register a derived_from relation instead.
    await relation_repository.create(
        {
            "from_id": fact.id,
            "to_id": raw.id,
            "to_name": raw.permalink,
            "relation_type": DERIVED_FROM_RELATION_TYPE,
        }
    )

    root = await build_drill_down_chain(
        entity_repository, relation_repository, fact, target_level="L0", max_depth=5
    )
    rendered = render_drill_down_chain(root)

    assert "raw-src" in rendered
    assert root.children, "expected the derived_from backref to produce a child"
    assert root.children[0].via == "derived_from"


@pytest.mark.asyncio
async def test_drill_down_stops_at_target_level(
    entity_repository, relation_repository
):
    """target_level=L1 stops descent before reaching L0."""
    raw = await _make_entity(
        entity_repository, permalink="raw2", title="Raw2", level="L0"
    )
    fact = await _make_entity(
        entity_repository,
        permalink="fact2",
        title="Fact2",
        level="L1",
        sources=["memory://entity/raw2"],
    )
    root = await build_drill_down_chain(
        entity_repository, relation_repository, fact, target_level="L1", max_depth=5
    )
    # Reached L1 (the start), so it should not descend to L0.
    assert root.level == "L1"
    assert root.children == []