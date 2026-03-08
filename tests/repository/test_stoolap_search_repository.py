"""Tests for StoolapSearchRepository.

Uses an in-memory Stoolap database.  Tests are automatically skipped if
stoolap-python is not installed in the current environment.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone

stoolap = pytest.importorskip("stoolap", reason="stoolap-python not installed")

from memopad.repository.stoolap_schema import STOOLAP_DDL
from memopad.repository.stoolap_entity_repository import StoolapEntityRepository
from memopad.repository.stoolap_search_repository import (
    StoolapSearchRepository,
    StoolapSearchResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stoolap_db():
    """Shared in-memory Stoolap DB with DDL applied and one project seeded."""
    db = await stoolap.AsyncDatabase.open(":memory:")
    for stmt in STOOLAP_DDL:
        await db.execute(stmt)
    await db.execute(
        "INSERT INTO project (id, name, path, is_active) VALUES (1, 'test', '/tmp/test', 1)"
    )
    yield db
    await db.close()


@pytest_asyncio.fixture
async def search_repo(stoolap_db):
    return StoolapSearchRepository(stoolap_db, project_id=1)


@pytest_asyncio.fixture
async def entity_repo(stoolap_db):
    return StoolapEntityRepository(stoolap_db, project_id=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_and_index(entity_repo, search_repo, suffix, title, content=""):
    """Insert an entity and index it in one step."""
    entity = await entity_repo.upsert_entity(
        {
            "title": title,
            "entity_type": "note",
            "file_path": f"notes/note_{suffix}.md",
            "permalink": f"notes/note-{suffix}",
            "content_type": "text/markdown",
        }
    )
    await search_repo.index_entity(entity, content=content)
    return entity


# ---------------------------------------------------------------------------
# Tests: init_search_index (no-op)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_search_index_is_noop(search_repo: StoolapSearchRepository):
    """init_search_index should not raise (schema already applied by DDL)."""
    await search_repo.init_search_index()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: index_entity + search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_empty_returns_empty(search_repo: StoolapSearchRepository):
    results = await search_repo.search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_index_entity_and_search_by_title(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    """Indexing an entity and searching by its title word should return it."""
    await _insert_and_index(entity_repo, search_repo, "s1", "Python Async Guide")
    results = await search_repo.search("Python")
    assert len(results) == 1
    assert results[0].title == "Python Async Guide"


@pytest.mark.asyncio
async def test_search_by_content(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    """Tokens from the content body should be searchable via content_stems."""
    await _insert_and_index(
        entity_repo, search_repo, "s2", "Rust Notes",
        content="Rust is a systems programming language focused on safety.",
    )
    results = await search_repo.search("systems programming")
    assert len(results) == 1
    assert results[0].title == "Rust Notes"


@pytest.mark.asyncio
async def test_search_multi_token_and(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    """Multiple tokens are AND-ed — both must appear somewhere in the entry."""
    await _insert_and_index(entity_repo, search_repo, "and1", "Async Python", "async guide")
    await _insert_and_index(entity_repo, search_repo, "and2", "Sync Django", "sync guide")

    # Both contain 'guide' but only one contains 'Async'
    results = await search_repo.search("Async guide")
    assert len(results) == 1
    assert "Async" in results[0].title


@pytest.mark.asyncio
async def test_search_type_filter(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
    stoolap_db,
):
    """types= filter restricts results to matching entity types."""
    e1 = await entity_repo.upsert_entity(
        {"title": "My Note", "entity_type": "note", "file_path": "n1.md", "content_type": "text/markdown"}
    )
    e2_data = await entity_repo.upsert_entity(
        {"title": "My Page", "entity_type": "page", "file_path": "p1.md", "content_type": "text/markdown"}
    )

    # Manually insert with type set
    now = datetime.now(timezone.utc).isoformat()
    await stoolap_db.execute(
        "INSERT INTO search_index (entity_id, project_id, title, type, content_stems, updated_at) "
        "VALUES (?, 1, ?, 'note', 'My Note content', ?)",
        [e1.id, e1.title, now],
    )
    await stoolap_db.execute(
        "INSERT INTO search_index (entity_id, project_id, title, type, content_stems, updated_at) "
        "VALUES (?, 1, ?, 'page', 'My Page content', ?)",
        [e2_data.id, e2_data.title, now],
    )

    notes = await search_repo.search("My", types=["note"])
    assert len(notes) == 1
    assert notes[0].type == "note"

    pages = await search_repo.search("My", types=["page"])
    assert len(pages) == 1
    assert pages[0].type == "page"

    both = await search_repo.search("My", types=["note", "page"])
    assert len(both) == 2


@pytest.mark.asyncio
async def test_search_limit_offset(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    """limit and offset correctly paginate results."""
    for i in range(5):
        await _insert_and_index(entity_repo, search_repo, f"pg{i}", f"Python Note {i}", "python")

    page1 = await search_repo.search("python", limit=2, offset=0)
    page2 = await search_repo.search("python", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # Non-overlapping
    ids_p1 = {r.id for r in page1}
    ids_p2 = {r.id for r in page2}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_search_no_match_returns_empty(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    await _insert_and_index(entity_repo, search_repo, "nomatch", "Python Guide", "async")
    results = await search_repo.search("javascript")
    assert results == []


# ---------------------------------------------------------------------------
# Tests: remove_entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_entity_clears_index(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    entity = await _insert_and_index(entity_repo, search_repo, "rm1", "To Remove", "content")
    # Verify it appears in search
    assert len(await search_repo.search("Remove")) == 1

    await search_repo.remove_entity(entity.id)
    assert len(await search_repo.search("Remove")) == 0


@pytest.mark.asyncio
async def test_remove_nonexistent_entity_is_safe(search_repo: StoolapSearchRepository):
    """Removing a non-existent entity ID should not raise."""
    await search_repo.remove_entity(99999)  # Should not raise


# ---------------------------------------------------------------------------
# Tests: index_entity updates existing index row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_entity_updates_on_reindex(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    """Calling index_entity twice on same entity should update, not create duplicate."""
    entity = await _insert_and_index(entity_repo, search_repo, "upd1", "Original Title", "first content")
    # Update entity then re-index
    entity.title = "Updated Title"
    await search_repo.index_entity(entity, content="second content")

    results = await search_repo.search("Updated")
    assert len(results) == 1
    assert results[0].title == "Updated Title"

    # Old title no longer searchable
    old_results = await search_repo.search("Original")
    assert len(old_results) == 0


# ---------------------------------------------------------------------------
# Tests: count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_empty(search_repo: StoolapSearchRepository):
    assert await search_repo.count() == 0


@pytest.mark.asyncio
async def test_count_with_query(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    await _insert_and_index(entity_repo, search_repo, "cnt1", "Python Guide", "python content")
    await _insert_and_index(entity_repo, search_repo, "cnt2", "Rust Guide", "rust content")

    assert await search_repo.count("python") == 1
    assert await search_repo.count("guide") == 2
    assert await search_repo.count("javascript") == 0


# ---------------------------------------------------------------------------
# Tests: StoolapSearchResult fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_result_has_expected_fields(
    entity_repo: StoolapEntityRepository,
    search_repo: StoolapSearchRepository,
):
    await _insert_and_index(entity_repo, search_repo, "fld1", "Field Test", "field check")
    results = await search_repo.search("field")
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, StoolapSearchResult)
    assert r.id is not None
    assert r.project_id == 1
    assert r.title == "Field Test"
    assert r.entity_id is not None
