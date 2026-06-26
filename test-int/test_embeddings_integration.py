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
from memopad.repository.entity_repository import EntityRepository
from memopad.repository.search_repository import create_search_repository
from memopad.services import embedding_service
from memopad.services.embedding_service import EmbeddingService, reset_provider_cache
from memopad.services.file_service import FileService
from memopad.services.search_service import SearchService


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


async def _count_embeddings(session_maker, entity_id):
    """Return the embedding row for an entity, or None if no row (or no table).

    When embeddings are disabled the table is never created, so a missing table
    is the expected "nothing was written" state — not an error.
    """
    async with session_maker() as session:
        exists = await session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding'")
        )
        if exists.fetchone() is None:
            return None
        result = await session.execute(
            text("SELECT entity_id, dim, model, vector FROM embedding WHERE entity_id = :eid"),
            {"eid": entity_id},
        )
        return result.fetchone()


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

    row = await _count_embeddings(session_maker, entity.id)
    assert row is not None, "embedding row was not populated by index_entity_markdown"
    assert row[0] == entity.id
    assert row[1] == FakeProvider.dim
    assert row[2] == FakeProvider.model_name
    assert row[3] is not None  # vector blob


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
        ("docs/python-web.md", "docs/python-web", "Python Web Development",
         "# Python Web Development\n\nBuilding web apps with Python Flask and Django.\n"),
        ("docs/javascript-frontend.md", "docs/javascript-frontend", "JavaScript Frontend",
         "# JavaScript Frontend\n\nBuilding user interfaces with JavaScript and React.\n"),
        ("docs/cooking-recipes.md", "docs/cooking-recipes", "Cooking Recipes",
         "# Cooking Recipes\n\nPasta, baking, and dessert recipes for home cooks.\n"),
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
    row = await _count_embeddings(session_maker, entity.id)
    assert row is None