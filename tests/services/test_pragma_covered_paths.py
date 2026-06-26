"""Tests for production-critical branches previously marked `# pragma: no cover`.

Phase 5.2: the optimax plan named two specific paths whose defensive branches
were excluded from coverage:
  - `entity_service._prepend_after_frontmatter` frontmatter-parse failure
    fallback (entity_service.py:1122-1131)
  - `search_service.index_entity_data` repository-failure re-raise
    (search_service.py:319-326)

These exercise real failure modes (malformed frontmatter, repository down), so
they are worth covering. The pragma markers are removed from the now-covered
branches in the source.
"""

from types import SimpleNamespace

import pytest

from memopad.services.entity_service import EntityService
from memopad.services.search_service import SearchService


# --------------------------------------------------------------------------
# _prepend_after_frontmatter
# --------------------------------------------------------------------------


def _bare_entity_service() -> EntityService:
    """_prepend_after_frontmatter doesn't touch self, so skip __init__."""
    return object.__new__(EntityService)


def test_prepend_no_frontmatter_adds_trailing_newline():
    svc = _bare_entity_service()
    out = svc._prepend_after_frontmatter("body text", "new content")
    # content has no trailing newline -> simple prepend inserts one
    assert out == "new content\nbody text"


def test_prepend_no_frontmatter_preserves_existing_newline():
    svc = _bare_entity_service()
    out = svc._prepend_after_frontmatter("body text", "new content\n")
    assert out == "new content\nbody text"


def test_prepend_with_valid_frontmatter_preserves_it():
    svc = _bare_entity_service()
    current = "---\ntitle: T\n---\n\nbody"
    out = svc._prepend_after_frontmatter(current, "added")
    assert out.startswith("---\n")  # frontmatter survives
    assert "added" in out
    assert "body" in out


def test_prepend_frontmatter_parse_failure_falls_back_to_simple(monkeypatch):
    """When frontmatter is present but parsing raises, fall back to a plain
    prepend rather than crashing (the defensive branch the pragma hid)."""
    svc = _bare_entity_service()

    def boom(_):
        raise RuntimeError("malformed frontmatter")

    monkeypatch.setattr("memopad.services.entity_service.parse_frontmatter", boom)

    current = "---\ntitle: T\n---\n\nbody"  # has_frontmatter() is True
    out = svc._prepend_after_frontmatter(current, "added")
    # Fallback: simple prepend (no trailing newline -> one is added)
    assert out == "added\n" + current


# --------------------------------------------------------------------------
# index_entity_data — repository failure re-raise
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_entity_data_reraises_on_repository_error():
    """A repository failure during reindex must propagate (background task
    contracts rely on it), not be swallowed."""

    class _BoomRepo:
        async def delete_by_entity_id(self, entity_id):
            raise RuntimeError("database down")

    svc = SearchService.__new__(SearchService)  # skip __init__
    svc.repository = _BoomRepo()

    entity = SimpleNamespace(
        id=42, permalink="notes/x", project_id=1, is_markdown=True
    )

    with pytest.raises(RuntimeError, match="database down"):
        await svc.index_entity_data(entity)