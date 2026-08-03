"""Integration tests for the incremental ``reindex_all``.

Verifies that ``SearchService.reindex_all`` (default incremental mode):

    * skips entities whose indexed output is unchanged since the last reindex
      (no FTS write, no embedding model call),
    * re-indexes changed/new entities,
    * prunes FTS rows and embedding vectors for entities that no longer exist,
    * still drops orphan vectors (the guarantee ``clear_project`` gave), and
    * falls back to a full wipe-and-rebuild under ``force=True``.

Runs against a real SQLite database via the test-int ``engine_factory`` fixture,
with a deterministic ``FakeProvider`` standing in for the embedding model.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from memopad.markdown import EntityParser
from memopad.markdown.markdown_processor import MarkdownProcessor
from memopad.repository import (
    EntityRepository,
    ObservationRepository,
    ProjectRepository,
    RelationRepository,
)
from memopad.repository.search_repository import create_search_repository
from memopad.schemas.search import SearchQuery
from memopad.services import embedding_service
from memopad.services.embedding_service import (
    ITEM_TYPE_ENTITY,
    EmbeddingService,
    reset_provider_cache,
)
from memopad.services.entity_service import EntityService
from memopad.services.file_service import FileService
from memopad.services.link_resolver import LinkResolver
from memopad.services.search_service import REINDEX_INDEX_VERSION, SearchService
from memopad.sync.sync_service import SyncService


class FakeProvider:
    """Deterministic stand-in for the fastembed provider (see test_embeddings_integration)."""

    model_name = "fake/deterministic"
    dim = 8

    def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for ch in (t or "").lower():
                if ch.isalpha():
                    vec[ord(ch) % self.dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    """Force embeddings on with a fake provider for every test in this module."""
    monkeypatch.setenv("MEMOPAD_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(embedding_service, "is_enabled", lambda: True)
    monkeypatch.setattr(embedding_service, "_get_provider", lambda *a, **k: FakeProvider())
    reset_provider_cache()
    yield
    reset_provider_cache()


@pytest.fixture
def upsert_counter(monkeypatch):
    """Count ``EmbeddingService.upsert_batch`` calls so we can assert that an
    incremental reindex skips embedding work without relying on the
    second-resolution ``updated_at`` column (which is flaky within one second)."""
    counter = {"n": 0}
    original = EmbeddingService.upsert_batch

    async def counting(self, items):
        counter["n"] += 1
        return await original(self, items)

    monkeypatch.setattr(EmbeddingService, "upsert_batch", counting)
    return counter


_PYTHON_NOTE = """# Python App

A small service written in Python.

## Observations
- [stack] Built with the Flask web framework
- [deploy] Containerised and shipped to production

## Relations
- depends_on [[Flask Framework]]
"""

_FLASK_NOTE = """# Flask Framework

A lightweight Python web framework.
"""

_SECOND_NOTE = """# Second Note

A totally separate note with no relations.

## Observations
- [kind] stand-alone entity
"""


def _build_sync_service(session_maker, project, app_config):
    """Wire a SyncService against the test session_maker (mirrors get_sync_service)."""
    base_path = Path(project.path)
    entity_parser = EntityParser(base_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(base_path, markdown_processor)

    entity_repository = EntityRepository(session_maker, project_id=project.id)
    observation_repository = ObservationRepository(session_maker, project_id=project.id)
    relation_repository = RelationRepository(session_maker, project_id=project.id)
    search_repository = create_search_repository(session_maker, project_id=project.id)
    project_repository = ProjectRepository(session_maker)

    search_service = SearchService(
        search_repository, entity_repository, file_service, session_maker, project.id
    )
    link_resolver = LinkResolver(entity_repository, search_service)
    entity_service = EntityService(
        entity_parser,
        entity_repository,
        observation_repository,
        relation_repository,
        file_service,
        link_resolver,
        search_service=search_service,
        app_config=app_config,
    )
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


async def _embedding_keys(session_maker, project_id):
    """Return the set of (item_type, item_id) embedded for a project."""
    async with session_maker() as session:
        exists = await session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding'")
        )
        if exists.fetchone() is None:
            return set()
        result = await session.execute(
            text("SELECT item_type, item_id FROM embedding WHERE project_id = :pid"),
            {"pid": project_id},
        )
        return {(r[0], r[1]) for r in result.fetchall()}


async def _reindex_state_rows(session_maker, project_id):
    """Return {entity_id: (fingerprint, index_version)} from reindex_state."""
    async with session_maker() as session:
        exists = await session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reindex_state'")
        )
        if exists.fetchone() is None:
            return {}
        result = await session.execute(
            text(
                "SELECT entity_id, fingerprint, index_version "
                "FROM reindex_state WHERE project_id = :pid"
            ),
            {"pid": project_id},
        )
        return {r[0]: (r[1], r[2]) for r in result.fetchall()}


async def _search_index_count_for_entity(session_maker, entity_id):
    """Count search_index rows tagged with ``entity_id`` (entity + obs + rel rows)."""
    async with session_maker() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM search_index WHERE entity_id = :eid"),
            {"eid": entity_id},
        )
        return result.scalar()


@pytest.mark.asyncio
async def test_reindex_skips_unchanged(engine_factory, test_project, app_config, upsert_counter):
    """A second incremental reindex with no changes skips embedding work and keeps state."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.file_service.write_file("flask-framework.md", _FLASK_NOTE)
    # Sync the target first so the source's relation resolves.
    await sync_service.sync_file("flask-framework.md", new=True)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")

    after_sync = upsert_counter["n"]

    # First reindex: empty state -> everything is "changed" -> embedded.
    await sync_service.search_service.reindex_all(batch_size=8)
    after_first = upsert_counter["n"]
    assert after_first > after_sync, "first reindex did not embed"

    state = await _reindex_state_rows(session_maker, project_id)
    assert python.id in state
    assert state[python.id][1] == REINDEX_INDEX_VERSION
    # Stored fingerprint matches a freshly computed one for the unchanged entity.
    assert state[python.id][0] == SearchService._entity_fingerprint(python)

    # Second reindex: nothing changed -> skipped, no embedding work.
    await sync_service.search_service.reindex_all(batch_size=8)
    after_second = upsert_counter["n"]
    assert after_second == after_first, "unchanged entity was re-embedded"

    # State preserved unchanged.
    state2 = await _reindex_state_rows(session_maker, project_id)
    assert state2[python.id][0] == state[python.id][0]


@pytest.mark.asyncio
async def test_reindex_reindexes_changed(engine_factory, test_project, app_config):
    """A metadata-only change (no file edit) is detected and re-indexed into FTS."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")

    await sync_service.search_service.reindex_all(batch_size=8)
    state = await _reindex_state_rows(session_maker, project_id)
    fingerprint_before = state[python.id][0]

    # Mutate entity-level fields without touching the file (changes the fingerprint).
    new_title = "Python App Renamed"
    await sync_service.entity_repository.update(
        python.id, {"title": new_title, "entity_metadata": {"tags": ["reindexed"]}}
    )
    python_after = await sync_service.entity_repository.get_by_file_path("python-app.md")
    assert SearchService._entity_fingerprint(python_after) != fingerprint_before

    await sync_service.search_service.reindex_all(batch_size=8)

    # The new title is now in the FTS index.
    results = await sync_service.search_service.search(SearchQuery(text="Renamed"))
    assert results, "renamed entity not found in FTS"
    assert any(r.id == python.id and r.type == ITEM_TYPE_ENTITY for r in results)

    # State fingerprint updated to the new one.
    state2 = await _reindex_state_rows(session_maker, project_id)
    assert state2[python.id][0] == SearchService._entity_fingerprint(python_after)


@pytest.mark.asyncio
async def test_reindex_prunes_deleted(engine_factory, test_project, app_config):
    """An entity deleted from the DB has its FTS rows and vectors pruned on the next reindex."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.file_service.write_file("flask-framework.md", _FLASK_NOTE)
    await sync_service.sync_file("flask-framework.md", new=True)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")
    flask = await sync_service.entity_repository.get_by_file_path("flask-framework.md")

    await sync_service.search_service.reindex_all(batch_size=8)
    assert await _search_index_count_for_entity(session_maker, python.id) > 0
    embedded = await _embedding_keys(session_maker, project_id)
    assert (ITEM_TYPE_ENTITY, python.id) in embedded

    # Delete python from the DB only (cascade drops its observations + outgoing
    # relation). Search index and embedding rows are left orphaned on purpose.
    await sync_service.entity_repository.delete(python.id)

    await sync_service.search_service.reindex_all(batch_size=8)

    # FTS rows for python gone; flask intact.
    assert await _search_index_count_for_entity(session_maker, python.id) == 0
    assert await _search_index_count_for_entity(session_maker, flask.id) > 0

    # Vectors for python (entity + its observations + its relation) gone; flask intact.
    embedded_after = await _embedding_keys(session_maker, project_id)
    assert (ITEM_TYPE_ENTITY, python.id) not in embedded_after
    assert (ITEM_TYPE_ENTITY, flask.id) in embedded_after

    # reindex_state row for python removed; flask retained.
    state = await _reindex_state_rows(session_maker, project_id)
    assert python.id not in state
    assert flask.id in state


@pytest.mark.asyncio
async def test_reindex_clears_stale_vectors(engine_factory, test_project, app_config):
    """Incremental reindex still prunes orphan vectors (the old clear_project guarantee)."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")

    # Plant a stale vector for an entity id no note owns.
    svc = await sync_service.search_service._get_embedding_service()
    assert svc is not None
    await svc.upsert_batch([(ITEM_TYPE_ENTITY, 999_999, "stale orphan content")])
    assert (ITEM_TYPE_ENTITY, 999_999) in await _embedding_keys(session_maker, project_id)

    await sync_service.search_service.reindex_all(batch_size=8)

    embedded = await _embedding_keys(session_maker, project_id)
    assert (ITEM_TYPE_ENTITY, 999_999) not in embedded, "stale vector survived reindex"
    assert (ITEM_TYPE_ENTITY, python.id) in embedded


@pytest.mark.asyncio
async def test_reindex_force_full_wipe(engine_factory, test_project, app_config, upsert_counter):
    """force=True re-embeds everything even when nothing changed."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")

    await sync_service.search_service.reindex_all(batch_size=8)
    after_incremental = upsert_counter["n"]
    assert await _reindex_state_rows(session_maker, project_id)  # state populated

    # Plant an orphan; force must clear it (clear_project) and re-embed everything.
    svc = await sync_service.search_service._get_embedding_service()
    await svc.upsert_batch([(ITEM_TYPE_ENTITY, 999_999, "stale orphan content")])

    await sync_service.search_service.reindex_all(batch_size=8, force=True)
    after_force = upsert_counter["n"]
    assert after_force > after_incremental, "force did not re-embed"

    embedded = await _embedding_keys(session_maker, project_id)
    assert (ITEM_TYPE_ENTITY, 999_999) not in embedded
    assert (ITEM_TYPE_ENTITY, python.id) in embedded

    # State rebuilt for the current entity.
    state = await _reindex_state_rows(session_maker, project_id)
    assert python.id in state
    assert state[python.id][1] == REINDEX_INDEX_VERSION


@pytest.mark.asyncio
async def test_reindex_version_bump_forces_reindex(
    engine_factory, test_project, app_config, upsert_counter
):
    """State rows carrying an older index_version are treated as stale and re-indexed."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)
    python = await sync_service.entity_repository.get_by_file_path("python-app.md")

    await sync_service.search_service.reindex_all(batch_size=8)
    after_first = upsert_counter["n"]

    # Simulate a schema/tokenizer bump: back-date the stored version.
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE reindex_state SET index_version = 0 WHERE project_id = :pid"
            ),
            {"pid": project_id},
        )
        await session.commit()

    await sync_service.search_service.reindex_all(batch_size=8)
    after_bump = upsert_counter["n"]
    assert after_bump > after_first, "stale-version entity was not re-indexed"

    state = await _reindex_state_rows(session_maker, project_id)
    assert state[python.id][1] == REINDEX_INDEX_VERSION