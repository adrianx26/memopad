"""Tests for StoolapEntityRepository.

Uses an in-memory Stoolap database to test the repository independently of
the rest of the memopad stack.  Tests are automatically skipped if
stoolap-python is not installed in the current environment.
"""

import pytest
import pytest_asyncio

stoolap = pytest.importorskip("stoolap", reason="stoolap-python not installed")

from memopad.repository.stoolap_schema import STOOLAP_DDL
from memopad.repository.stoolap_entity_repository import (
    StoolapEntityRepository,
    StoolapEntity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stoolap_db():
    """Open a fresh in-memory Stoolap database with schema applied."""
    db = await stoolap.AsyncDatabase.open(":memory:")
    for stmt in STOOLAP_DDL:
        await db.execute(stmt)

    # Seed a project row (project_id = 1)
    await db.execute(
        "INSERT INTO project (id, name, path, permalink, is_active) VALUES (1, 'test', '/tmp/test', 'test', 1)"
    )
    yield db
    await db.close()


@pytest_asyncio.fixture
async def repo(stoolap_db):
    """StoolapEntityRepository scoped to project_id=1."""
    return StoolapEntityRepository(stoolap_db, project_id=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_data(suffix: str = "1", entity_type: str = "note") -> dict:
    return {
        "title": f"Note {suffix}",
        "entity_type": entity_type,
        "file_path": f"notes/note_{suffix}.md",
        "permalink": f"notes/note-{suffix}",
        "content_type": "text/markdown",
        "checksum": f"abc{suffix}",
    }


# ---------------------------------------------------------------------------
# Tests: insert / upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_new_entity(repo: StoolapEntityRepository):
    """upsert_entity inserts a new row when file_path doesn't exist."""
    entity = await repo.upsert_entity(_entity_data("1"))

    assert entity.id is not None
    assert entity.id > 0
    assert entity.title == "Note 1"
    assert entity.file_path == "notes/note_1.md"
    assert entity.permalink == "notes/note-1"
    assert entity.project_id == 1


@pytest.mark.asyncio
async def test_upsert_updates_existing_entity(repo: StoolapEntityRepository):
    """upsert_entity updates an existing row keyed on file_path + project_id."""
    original = await repo.upsert_entity(_entity_data("2"))
    assert original.title == "Note 2"

    # Upsert again with same file_path but different title
    updated = await repo.upsert_entity(
        {**_entity_data("2"), "title": "Renamed Note 2"}
    )
    assert updated.id == original.id  # same row
    assert updated.title == "Renamed Note 2"


@pytest.mark.asyncio
async def test_upsert_assigns_external_id(repo: StoolapEntityRepository):
    """upsert_entity assigns a UUID external_id if not provided."""
    entity = await repo.upsert_entity(_entity_data("ext"))
    assert entity.external_id is not None
    assert len(entity.external_id) == 36  # UUID4 format


@pytest.mark.asyncio
async def test_upsert_preserves_provided_external_id(repo: StoolapEntityRepository):
    """upsert_entity keeps an explicitly provided external_id."""
    data = {**_entity_data("ext2"), "external_id": "my-custom-uuid-1234"}
    entity = await repo.upsert_entity(data)
    assert entity.external_id == "my-custom-uuid-1234"


# ---------------------------------------------------------------------------
# Tests: reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id(repo: StoolapEntityRepository):
    inserted = await repo.upsert_entity(_entity_data("g1"))
    found = await repo.get_by_id(inserted.id)

    assert found is not None
    assert found.id == inserted.id
    assert found.title == inserted.title


@pytest.mark.asyncio
async def test_get_by_id_missing_returns_none(repo: StoolapEntityRepository):
    found = await repo.get_by_id(99999)
    assert found is None


@pytest.mark.asyncio
async def test_get_by_permalink(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("pl"))
    found = await repo.get_by_permalink("notes/note-pl")
    assert found is not None
    assert found.permalink == "notes/note-pl"


@pytest.mark.asyncio
async def test_get_by_permalink_missing_returns_none(repo: StoolapEntityRepository):
    found = await repo.get_by_permalink("no/such/permalink")
    assert found is None


@pytest.mark.asyncio
async def test_get_by_file_path(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("fp"))
    found = await repo.get_by_file_path("notes/note_fp.md")
    assert found is not None
    assert found.file_path == "notes/note_fp.md"


@pytest.mark.asyncio
async def test_get_by_file_path_missing_returns_none(repo: StoolapEntityRepository):
    found = await repo.get_by_file_path("nonexistent/file.md")
    assert found is None


@pytest.mark.asyncio
async def test_get_by_external_id(repo: StoolapEntityRepository):
    eid = "unique-ext-id-xyz"
    await repo.upsert_entity({**_entity_data("eid"), "external_id": eid})
    found = await repo.get_by_external_id(eid)
    assert found is not None
    assert found.external_id == eid


@pytest.mark.asyncio
async def test_find_all_empty(repo: StoolapEntityRepository):
    results = await repo.find_all()
    assert results == []


@pytest.mark.asyncio
async def test_find_all_returns_entities(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("a1"))
    await repo.upsert_entity(_entity_data("a2"))
    results = await repo.find_all()
    assert len(results) == 2


@pytest.mark.asyncio
async def test_find_all_entity_type_filter(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("n1", entity_type="note"))
    await repo.upsert_entity(_entity_data("p1", entity_type="page"))
    notes = await repo.find_all(entity_type="note")
    assert len(notes) == 1
    assert notes[0].entity_type == "note"


@pytest.mark.asyncio
async def test_find_all_limit(repo: StoolapEntityRepository):
    for i in range(5):
        await repo.upsert_entity(_entity_data(str(i)))
    results = await repo.find_all(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_get_all_file_paths(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("fp1"))
    await repo.upsert_entity(_entity_data("fp2"))
    paths = await repo.get_all_file_paths()
    assert "notes/note_fp1.md" in paths
    assert "notes/note_fp2.md" in paths


@pytest.mark.asyncio
async def test_find_by_directory_prefix(repo: StoolapEntityRepository):
    await repo.upsert_entity(
        {**_entity_data("d1"), "file_path": "docs/guide.md", "title": "Guide"}
    )
    await repo.upsert_entity(
        {**_entity_data("d2"), "file_path": "docs/api/ref.md", "title": "Ref"}
    )
    await repo.upsert_entity(
        {**_entity_data("d3"), "file_path": "specs/spec.md", "title": "Spec"}
    )

    docs_results = await repo.find_by_directory_prefix("docs")
    assert len(docs_results) == 2
    file_paths = {e.file_path for e in docs_results}
    assert file_paths == {"docs/guide.md", "docs/api/ref.md"}

    api_results = await repo.find_by_directory_prefix("docs/api")
    assert len(api_results) == 1
    assert api_results[0].file_path == "docs/api/ref.md"


@pytest.mark.asyncio
async def test_count(repo: StoolapEntityRepository):
    assert await repo.count() == 0
    await repo.upsert_entity(_entity_data("c1"))
    await repo.upsert_entity(_entity_data("c2"))
    assert await repo.count() == 2


@pytest.mark.asyncio
async def test_count_by_type(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("t1", entity_type="note"))
    await repo.upsert_entity(_entity_data("t2", entity_type="page"))
    assert await repo.count("note") == 1
    assert await repo.count("page") == 1


# ---------------------------------------------------------------------------
# Tests: deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_by_file_path(repo: StoolapEntityRepository):
    await repo.upsert_entity(_entity_data("del1"))
    deleted = await repo.delete_by_file_path("notes/note_del1.md")
    assert deleted is True
    found = await repo.get_by_file_path("notes/note_del1.md")
    assert found is None


@pytest.mark.asyncio
async def test_delete_by_file_path_nonexistent_returns_false(repo: StoolapEntityRepository):
    result = await repo.delete_by_file_path("no/such/file.md")
    assert result is False


@pytest.mark.asyncio
async def test_delete_by_id(repo: StoolapEntityRepository):
    entity = await repo.upsert_entity(_entity_data("del2"))
    result = await repo.delete_by_id(entity.id)
    assert result is True
    assert await repo.get_by_id(entity.id) is None


@pytest.mark.asyncio
async def test_delete_by_id_nonexistent_returns_false(repo: StoolapEntityRepository):
    result = await repo.delete_by_id(99999)
    assert result is False


# ---------------------------------------------------------------------------
# Tests: project isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_isolation(stoolap_db):
    """Entities from project 2 are not visible in project 1's repository."""
    # Insert project 2
    await stoolap_db.execute(
        "INSERT INTO project (id, name, path, is_active) VALUES (2, 'other', '/tmp/other', 1)"
    )

    repo1 = StoolapEntityRepository(stoolap_db, project_id=1)
    repo2 = StoolapEntityRepository(stoolap_db, project_id=2)

    await repo1.upsert_entity(_entity_data("iso"))
    await repo2.upsert_entity(_entity_data("iso"))  # same file_path, diff project

    r1 = await repo1.find_all()
    r2 = await repo2.find_all()
    assert len(r1) == 1
    assert len(r2) == 1
    assert r1[0].project_id == 1
    assert r2[0].project_id == 2
