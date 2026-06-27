"""Integration test for the embeddings feature (Phase 3 of optimax.md).

Verifies the full wiring that ships embeddings:

    SearchService.index_entity_markdown
        -> EmbeddingService.upsert  (writes the `embedding` table)
    SearchService.hybrid_search(mode="semantic")
        -> EmbeddingService.similar  (reads the `embedding` table) + RRF

This runs against a real SQLite database (the test-int `engine_factory`
fixture) so the table creation, BLOB packing, and SQL are exercised for real.
The only thing faked is the embedding *model*: `fastembed` is an optional extra
that isn't installed in CI, so we swap in a deterministic `FakeProvider` via
monkeypatch. That keeps the test focused on the wiring (the part we wrote)
rather than the external model download.

Set MEMOPAD_EMBEDDINGS_ENABLED=true in the environment to exercise the real
`is_enabled()` gate; the test also forces it on explicitly.
"""

from datetime import datetime, timezone
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
from memopad.services import embedding_service
from memopad.services.embedding_service import (
    ITEM_TYPE_ENTITY,
    ITEM_TYPE_OBSERVATION,
    ITEM_TYPE_RELATION,
    EmbeddingService,
    reset_provider_cache,
)
from memopad.services.entity_service import EntityService
from memopad.services.file_service import FileService
from memopad.services.link_resolver import LinkResolver
from memopad.services.search_service import SearchService
from memopad.sync.sync_service import SyncService

try:  # sqlite-vec is an optional extra; tests that need vec0 skip without it.
    import sqlite_vec  # noqa: F401

    _HAS_SQLITE_VEC = True
except ImportError:  # pragma: no cover - depends on the embeddings extra
    _HAS_SQLITE_VEC = False


class FakeProvider:
    """Deterministic, dependency-free stand-in for the fastembed provider.

    Maps text to a fixed-dim vector by accumulating lowercase letter counts per
    bucket (ord(ch) % dim), then L2-normalizing. Similar text -> similar vectors,
    so cosine ranking is meaningful without a real model.
    """

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
    # The provider cache could hold a real provider from another test; clear it.
    reset_provider_cache()
    yield
    reset_provider_cache()


async def _embedding_rows(session_maker, project_id, item_type=None, item_id=None):
    """Return embedding rows for a project, optionally filtered by (item_type, item_id).

    Rows are (item_type, item_id, dim, model). When embeddings are disabled the
    table is never created, so a missing table is the expected "nothing was
    written" state — returns an empty list, not an error.
    """
    async with session_maker() as session:
        exists = await session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding'")
        )
        if exists.fetchone() is None:
            return []
        sql = "SELECT item_type, item_id, dim, model FROM embedding WHERE project_id = :pid"
        params: dict = {"pid": project_id}
        if item_type is not None:
            sql += " AND item_type = :t"
            params["t"] = item_type
        if item_id is not None:
            sql += " AND item_id = :iid"
            params["iid"] = item_id
        result = await session.execute(text(sql), params)
        return result.fetchall()


def _build_sync_service(session_maker, project, app_config):
    """Wire a SyncService against the test session_maker (mirrors get_sync_service
    but without touching the global db / ConfigManager state)."""
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


@pytest.mark.asyncio
async def test_indexing_writes_embedding_table_row(engine_factory, test_project):
    """index_entity_markdown must upsert a vector into the embedding table."""
    engine, session_maker = engine_factory
    project_id = test_project.id
    base_path = Path(test_project.path)

    entity_repository = EntityRepository(session_maker, project_id=project_id)
    search_repository = create_search_repository(session_maker, project_id=project_id)
    entity_parser = EntityParser(base_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(base_path, markdown_processor)

    search_service = SearchService(
        search_repository, entity_repository, file_service, session_maker, project_id
    )
    await search_service.init_search_index()

    # Create an entity row and write its markdown file.
    content = "# Python Programming\n\nFlask and Django web frameworks for Python.\n"
    file_path = "docs/python-programming.md"
    await file_service.write_file(file_path, content)
    entity = await entity_repository.create(
        {
            "project_id": project_id,
            "title": "Python Programming",
            "entity_type": "entity",
            "permalink": "docs/python-programming",
            "file_path": file_path,
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    # Index it — this is the path we wired to also upsert the embedding.
    await search_service.index_entity_data(entity, content=content)

    rows = await _embedding_rows(
        session_maker, project_id, item_type=ITEM_TYPE_ENTITY, item_id=entity.id
    )
    assert rows, "embedding row was not populated by index_entity_markdown"
    row = rows[0]
    assert (row[0], row[1]) == (ITEM_TYPE_ENTITY, entity.id)
    assert row[2] == FakeProvider.dim
    assert row[3] == FakeProvider.model_name


@pytest.mark.asyncio
async def test_hybrid_search_returns_semantic_hit(engine_factory, test_project):
    """hybrid_search(mode='semantic') must surface an indexed note by similarity."""
    engine, session_maker = engine_factory
    project_id = test_project.id
    base_path = Path(test_project.path)

    entity_repository = EntityRepository(session_maker, project_id=project_id)
    search_repository = create_search_repository(session_maker, project_id=project_id)
    entity_parser = EntityParser(base_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(base_path, markdown_processor)

    search_service = SearchService(
        search_repository, entity_repository, file_service, session_maker, project_id
    )
    await search_service.init_search_index()

    notes = [
        (
            "docs/python-web.md",
            "docs/python-web",
            "Python Web Development",
            "# Python Web Development\n\nBuilding web apps with Python Flask and Django.\n",
        ),
        (
            "docs/javascript-frontend.md",
            "docs/javascript-frontend",
            "JavaScript Frontend",
            "# JavaScript Frontend\n\nBuilding user interfaces with JavaScript and React.\n",
        ),
        (
            "docs/cooking-recipes.md",
            "docs/cooking-recipes",
            "Cooking Recipes",
            "# Cooking Recipes\n\nPasta, baking, and dessert recipes for home cooks.\n",
        ),
    ]
    entity_ids = []
    for file_path, permalink, title, content in notes:
        await file_service.write_file(file_path, content)
        entity = await entity_repository.create(
            {
                "project_id": project_id,
                "title": title,
                "entity_type": "entity",
                "permalink": permalink,
                "file_path": file_path,
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await search_service.index_entity_data(entity, content=content)
        entity_ids.append(entity.id)

    # A query about python web frameworks should rank the Python Web note first
    # among the semantic hits (FakeProvider is letter-bucket based, so "python"
    # and "web" dominate its vector).
    results = await search_service.hybrid_search(
        query_text="python web framework",
        mode="semantic",
        limit=5,
        session_maker=session_maker,
        project_id=project_id,
    )

    assert len(results) >= 1, "semantic search returned no hits"
    # The top result must be the Python Web note.
    assert results[0].permalink == "docs/python-web"
    assert results[0].entity_id in entity_ids


@pytest.mark.asyncio
async def test_disabled_embeddings_is_noop_for_indexing(engine_factory, test_project, monkeypatch):
    """When embeddings are off, indexing must still succeed and write no vector."""
    # Override the autouse fake-on fixture for this one test.
    monkeypatch.setenv("MEMOPAD_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setattr(embedding_service, "is_enabled", lambda: False)

    engine, session_maker = engine_factory
    project_id = test_project.id
    base_path = Path(test_project.path)

    entity_repository = EntityRepository(session_maker, project_id=project_id)
    search_repository = create_search_repository(session_maker, project_id=project_id)
    entity_parser = EntityParser(base_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(base_path, markdown_processor)

    search_service = SearchService(
        search_repository, entity_repository, file_service, session_maker, project_id
    )
    await search_service.init_search_index()

    content = "# Some Note\n\nContent here.\n"
    await file_service.write_file("docs/some-note.md", content)
    entity = await entity_repository.create(
        {
            "project_id": project_id,
            "title": "Some Note",
            "entity_type": "entity",
            "permalink": "docs/some-note",
            "file_path": "docs/some-note.md",
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    # Must not raise even though the embedding table may not exist yet.
    await search_service.index_entity_data(entity, content=content)

    # FTS index still got the row.
    from memopad.schemas.search import SearchQuery

    fts = await search_service.search(SearchQuery(text="Some Note"))
    assert any(r.permalink == "docs/some-note" for r in fts)

    # And no embedding row was written.
    rows = await _embedding_rows(
        session_maker, project_id, item_type=ITEM_TYPE_ENTITY, item_id=entity.id
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Facts (observations) + relations get their own vectors (G4), and the sync
# file-delete path clears them (G1). These drive the real SyncService path so
# the entity is parsed, persisted with observations + a resolved relation,
# indexed, and embedded exactly as in production.
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_sync_writes_observation_and_relation_vectors(
    engine_factory, test_project, app_config
):
    """index_entity (via sync) must embed the entity + each observation + each relation."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    # Target first so the source's `depends_on [[Flask Framework]]` resolves.
    await sync_service.file_service.write_file("flask-framework.md", _FLASK_NOTE)
    await sync_service.sync_file("flask-framework.md", new=True)
    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    entity, _ = await sync_service.sync_file("python-app.md", new=True)
    assert entity is not None

    # Reload with observations + outgoing relations eager-loaded (sync_file returns
    # a loaded entity, but be explicit to read ids deterministically).
    loaded = await sync_service.entity_repository.get_by_file_path("python-app.md")
    obs_ids = [o.id for o in loaded.observations]
    rel_ids = [r.id for r in loaded.outgoing_relations]
    assert obs_ids, "parser produced no observations"
    assert rel_ids, "parser produced no outgoing relations"

    rows = await _embedding_rows(session_maker, project_id)
    keys = {(r[0], r[1]) for r in rows}
    assert (ITEM_TYPE_ENTITY, loaded.id) in keys
    for oid in obs_ids:
        assert (ITEM_TYPE_OBSERVATION, oid) in keys
    for rid in rel_ids:
        assert (ITEM_TYPE_RELATION, rid) in keys


@pytest.mark.asyncio
async def test_sync_delete_clears_vectors(engine_factory, test_project, app_config):
    """G1: handle_delete (file-sync delete) must remove the entity's vectors too."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("flask-framework.md", _FLASK_NOTE)
    await sync_service.sync_file("flask-framework.md", new=True)
    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)

    loaded = await sync_service.entity_repository.get_by_file_path("python-app.md")
    keys_before = {
        (ITEM_TYPE_ENTITY, loaded.id),
        *((ITEM_TYPE_OBSERVATION, o.id) for o in loaded.observations),
        *((ITEM_TYPE_RELATION, r.id) for r in loaded.outgoing_relations),
    }
    rows = await _embedding_rows(session_maker, project_id)
    assert keys_before.issubset({(r[0], r[1]) for r in rows})

    # Simulate the file being removed and sync noticing.
    await sync_service.handle_delete("python-app.md")

    rows_after = await _embedding_rows(session_maker, project_id)
    after_keys = {(r[0], r[1]) for r in rows_after}
    assert keys_before.isdisjoint(after_keys), "vectors orphaned after sync delete"
    # The unrelated Flask note's entity vector must survive.
    flask = await sync_service.entity_repository.get_by_file_path("flask-framework.md")
    assert (ITEM_TYPE_ENTITY, flask.id) in after_keys


@pytest.mark.asyncio
async def test_reindex_clears_stale_vectors(engine_factory, test_project, app_config):
    """G2: reindex_all clears stale vectors and re-populates from current notes."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()

    await sync_service.file_service.write_file("python-app.md", _PYTHON_NOTE)
    await sync_service.sync_file("python-app.md", new=True)
    loaded = await sync_service.entity_repository.get_by_file_path("python-app.md")

    # Plant a stale vector for an entity id that no note owns.
    svc = await sync_service.search_service._get_embedding_service()
    assert svc is not None
    await svc.upsert_batch([(ITEM_TYPE_ENTITY, 999_999, "stale orphan content")])
    assert await _embedding_rows(
        session_maker, project_id, item_type=ITEM_TYPE_ENTITY, item_id=999_999
    )

    # Full reindex: clears the project's vectors, then re-embeds every note.
    await sync_service.search_service.reindex_all(batch_size=8)

    rows = await _embedding_rows(session_maker, project_id)
    keys = {(r[0], r[1]) for r in rows}
    assert (ITEM_TYPE_ENTITY, 999_999) not in keys, "stale vector survived reindex"
    assert (ITEM_TYPE_ENTITY, loaded.id) in keys
    assert all((ITEM_TYPE_OBSERVATION, o.id) in keys for o in loaded.observations), (
        "observations not re-embedded by reindex"
    )


# ---------------------------------------------------------------------------
# Semantic search surfaces an observation-level match (a specific fact), not
# just the parent note — the core point of embedding facts + relations (G4).
# FakeProvider maps text to letter-bucket vectors, so we craft a fact whose
# text dominates a bucket the parent note's title/body/headers only touch
# weakly, forcing the observation to rank above its own entity.
# ---------------------------------------------------------------------------


_OBS_NOTE = """# Music

mmmm uuuu ssss iiii cccc aaaa.

## Observations
- [z] zzz zzz zzz zzz zzz zzz zzz zzz zzz zzz
"""


@pytest.mark.asyncio
async def test_semantic_search_ranks_observation_first(engine_factory, test_project, app_config):
    engine, session_maker = engine_factory
    project_id = test_project.id

    sync_service = _build_sync_service(session_maker, test_project, app_config)
    await sync_service.search_service.init_search_index()
    await sync_service.file_service.write_file("music.md", _OBS_NOTE)
    await sync_service.sync_file("music.md", new=True)

    results = await sync_service.search_service.hybrid_search(
        query_text="zzz",
        mode="semantic",
        limit=5,
        session_maker=session_maker,
        project_id=project_id,
    )
    assert results, "semantic search returned no hits"
    # The top hit must be the observation (a fact), not the parent entity.
    assert results[0].type == ITEM_TYPE_OBSERVATION, (
        f"expected an observation ranked first, got type={results[0].type!r} "
        f"permalink={results[0].permalink!r}"
    )
    assert "zzz" in (results[0].content_snippet or "")


# ---------------------------------------------------------------------------
# vec0 ANN path — only exercised when the sqlite-vec extension is importable
# (the embeddings extra). The autouse fixture sets MEMOPAD_EMBEDDINGS_ENABLED
# so the SQLite connect hook loads the extension; FILESYSTEM + NullPool gives
# each session a fresh connection that actually has vec0 available.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec extra not installed")
async def test_vec0_ann_index_used_when_available(engine_factory, test_project):
    """When sqlite-vec loads, the service builds vec0 tables and KNNs through them."""
    engine, session_maker = engine_factory
    project_id = test_project.id

    svc = EmbeddingService(session_maker, project_id, provider=FakeProvider())
    await svc.init_store()
    assert svc._use_vec0, "sqlite-vec present but vec0 index was not enabled"

    # The per-type vec0 tables must exist for this project.
    async with session_maker() as session:
        for t in ("entity", "observation", "relation"):
            tbl = f"embedding_vec_{t}_p{project_id}"
            exists = await session.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
                {"n": tbl},
            )
            assert exists.fetchone() is not None, f"missing vec0 table {tbl}"

    await svc.upsert_batch(
        [
            (ITEM_TYPE_ENTITY, 1, "python web framework flask"),
            (ITEM_TYPE_ENTITY, 2, "cooking pasta baking recipes"),
        ]
    )
    hits = await svc.similar("python web", limit=2)
    assert hits
    # KNN returns cosine distance converted to similarity; the python entity wins.
    assert (hits[0].item_type, hits[0].item_id) == (ITEM_TYPE_ENTITY, 1)
    assert hits[0].score > 0  # similarity, not distance
