"""Repo-level tests for the CodeGraph service (Tb G2).

Indexes a tiny source tree through the real repositories (SQLite) and exercises
the queries end-to-end: entities + relations persisted, find_symbol, impact_path,
code_context. The feature gate (codegraph_enabled) is also covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memopad.importers.code_importer import (
    ENTITY_CLASS,
    ENTITY_FILE,
    ENTITY_FUNCTION,
    ENTITY_MODULE,
    REL_CALLS,
    REL_DEFINED_IN,
)
from memopad.services.codegraph_service import CodeGraphService, DEFINITION_CATEGORY


@pytest.fixture
def code_tree(tmp_path: Path) -> Path:
    """A 2-file tree: a.py defines `shared`; b.py imports a and calls shared."""
    (tmp_path / "a.py").write_text(
        "def shared(x):\n"
        "    return x + 1\n"
        "\n"
        "class Thing:\n"
        "    pass\n"
    )
    (tmp_path / "b.py").write_text(
        "from a import shared\n"
        "\n"
        "def caller(n):\n"
        "    return shared(n)\n"
    )
    return tmp_path


def _service(
    entity_repository,
    observation_repository,
    relation_repository,
    search_repository,
    test_project,
    app_config,
):
    return CodeGraphService(
        entity_repository,
        observation_repository,
        relation_repository,
        search_repository,
        project_id=test_project.id,
        project_name=test_project.name,
        app_config=app_config,
    )


@pytest.mark.asyncio
async def test_index_directory_persists_entities_and_relations(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)

    report = await svc.index_directory(code_tree)
    assert report.files == 2
    assert report.entities > 0
    assert report.relations > 0

    # Entity types present (file/module/function/class).
    types = set()
    for etype in (ENTITY_FILE, ENTITY_MODULE, ENTITY_FUNCTION, ENTITY_CLASS):
        found = await entity_repository.list_by_entity_type(etype)
        types.update(e.entity_type for e in found)
    assert {ENTITY_FILE, ENTITY_MODULE, ENTITY_FUNCTION, ENTITY_CLASS} <= types

    # The `shared` function entity exists with a code:// permalink.
    fns = await entity_repository.list_by_entity_type(ENTITY_FUNCTION)
    shared = next(e for e in fns if e.title == "shared")
    assert shared.permalink == f"code://{test_project.name}/a.py::shared"
    assert shared.entity_metadata["qualified_name"] == "a.shared"

    # A resolved `calls` relation: caller -> shared.
    calls = await relation_repository.find_by_type(REL_CALLS)
    resolved = [r for r in calls if r.to_id is not None]
    assert any(r.to_name == "shared" for r in resolved)

    # defined_in relations exist.
    defined = await relation_repository.find_by_type(REL_DEFINED_IN)
    assert len(defined) > 0


@pytest.mark.asyncio
async def test_index_directory_is_idempotent(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    r1 = await svc.index_directory(code_tree)
    r2 = await svc.index_directory(code_tree)
    # Second run upserts the same entities (no explosion) and replaces relations.
    assert r1.entities == r2.entities
    calls = await relation_repository.find_by_type(REL_CALLS)
    # No duplicate resolved caller->shared edges.
    resolved = [r for r in calls if r.to_name == "shared" and r.to_id is not None]
    assert len(resolved) == 1


@pytest.mark.asyncio
async def test_index_directory_disabled_is_noop(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = False  # default
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    report = await svc.index_directory(code_tree)
    assert report.entities == 0
    fns = await entity_repository.list_by_entity_type(ENTITY_FUNCTION)
    assert fns == []


@pytest.mark.asyncio
async def test_find_symbol_and_impact_path_end_to_end(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    await svc.index_directory(code_tree)

    hits = await svc.find_symbol("shared")
    assert any(h.title == "shared" for h in hits)
    shared_permalink = f"code://{test_project.name}/a.py::shared"

    ip = await svc.impact_path(shared_permalink)
    # caller (in b.py) calls shared -> direct dependent at hop 1.
    assert shared_permalink not in ip.distances
    caller_permalink = f"code://{test_project.name}/b.py::caller"
    assert ip.distances.get(caller_permalink) == 1


@pytest.mark.asyncio
async def test_code_context_includes_definition(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    await svc.index_directory(code_tree)

    shared_permalink = f"code://{test_project.name}/a.py::shared"
    ctx = await svc.code_context(shared_permalink)
    assert ctx is not None
    assert "def shared" in ctx.definition
    # `caller` (in b.py) calls `shared`, so shared's context lists caller as a caller.
    caller_permalink = f"code://{test_project.name}/b.py::caller"
    assert caller_permalink in ctx.callers

    # Definition observation stored under the definition category.
    shared = await entity_repository.get_by_permalink(shared_permalink)
    obs = await observation_repository.find_by_entity(shared.id)
    assert any(o.category == DEFINITION_CATEGORY for o in obs)


@pytest.mark.asyncio
async def test_render_find_symbol_markdown(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    await svc.index_directory(code_tree)
    out = await svc.render_find_symbol("shared")
    assert "shared" in out
    assert "code://" in out
    assert await svc.render_find_symbol("nope") == "No symbols found matching 'nope'."


@pytest.mark.asyncio
async def test_render_impact_path_unknown_permalink(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    await svc.index_directory(code_tree)
    out = await svc.render_impact_path("code://test-project/missing.py::x")
    assert "No code symbol" in out


@pytest.mark.asyncio
async def test_index_directory_prunes_symbols_of_deleted_file(
    entity_repository, observation_repository, relation_repository, search_repository,
    test_project, app_config, code_tree,
):
    """Deleting a .py file between reindexes must prune its code entities —
    the file/module/function/class rows, their definition observations, and
    their search rows. Without prune, find_symbol/code_context would surface
    symbols whose source no longer exists (the gap the watch reindex hook
    relies on index_directory to close)."""
    app_config.codegraph_enabled = True
    svc = _service(entity_repository, observation_repository, relation_repository,
                   search_repository, test_project, app_config)
    await svc.index_directory(code_tree)

    shared_permalink = f"code://{test_project.name}/a.py::shared"
    file_permalink = f"code://{test_project.name}/a.py"
    shared = await entity_repository.get_by_permalink(shared_permalink)
    assert shared is not None
    shared_id = shared.id
    # A definition observation exists for shared before prune.
    obs_before = await observation_repository.find_by_entity(shared_id)
    assert any(o.category == DEFINITION_CATEGORY for o in obs_before)
    assert await entity_repository.get_by_permalink(file_permalink) is not None

    # Remove a.py from the tree and reindex.
    (code_tree / "a.py").unlink()
    report = await svc.index_directory(code_tree)
    assert report.pruned > 0  # a.py's file/module/function/class entities pruned

    # Entity rows gone (file + symbol), definition observation cascade-deleted,
    # and find_symbol no longer surfaces the deleted symbol.
    assert await entity_repository.get_by_permalink(shared_permalink) is None
    assert await entity_repository.get_by_permalink(file_permalink) is None
    obs_after = await observation_repository.find_by_entity(shared_id)
    assert obs_after == []  # DB-level ON DELETE CASCADE removed the observation
    hits = await svc.find_symbol("shared")
    assert not any(h.permalink == shared_permalink for h in hits)

    # Prune did not over-delete: b.py's caller is still present.
    caller_permalink = f"code://{test_project.name}/b.py::caller"
    assert await entity_repository.get_by_permalink(caller_permalink) is not None


@pytest.mark.asyncio
async def test_load_graph_view_excludes_other_projects_relations(
    entity_repository, observation_repository, relation_repository, search_repository,
    project_repository, test_project, app_config, session_maker, code_tree, tmp_path,
):
    """`relation_repository.find_by_type` is not project-scoped, so when >1
    project is code-graph-indexed in the same DB it returns every project's
    relations. `_load_graph_view` must filter by `project_id`, otherwise a
    foreign project's code edges leak into this project's graph view (and from
    there into find_symbol/impact_path/code_context)."""
    from memopad.repository.entity_repository import EntityRepository
    from memopad.repository.observation_repository import ObservationRepository
    from memopad.repository.relation_repository import RelationRepository
    from memopad.repository.sqlite_search_repository import SQLiteSearchRepository

    app_config.codegraph_enabled = True
    svc_a = _service(entity_repository, observation_repository, relation_repository,
                     search_repository, test_project, app_config)
    await svc_a.index_directory(code_tree)

    # A second project in the SAME db, with its own code graph.
    proj_b = await project_repository.create({
        "name": "other-project", "description": "B",
        "path": str(tmp_path / "b"), "is_active": True, "is_default": False,
    })
    b_tree = tmp_path / "b_src"
    b_tree.mkdir()
    (b_tree / "x.py").write_text("def alpha(n):\n    return n\n")
    (b_tree / "y.py").write_text(
        "from x import alpha\n" "\n" "def beta(n):\n" "    return alpha(n)\n"
    )
    svc_b = CodeGraphService(
        EntityRepository(session_maker, project_id=proj_b.id),
        ObservationRepository(session_maker, project_id=proj_b.id),
        RelationRepository(session_maker, project_id=proj_b.id),
        SQLiteSearchRepository(session_maker, project_id=proj_b.id),
        project_id=proj_b.id, project_name=proj_b.name, app_config=app_config,
    )
    await svc_b.index_directory(b_tree)

    # find_by_type genuinely returns relations from BOTH projects (no scope) —
    # this is the leak the service-level filter must contain.
    all_calls = await relation_repository.find_by_type(REL_CALLS)
    assert any(r.project_id == test_project.id for r in all_calls)
    assert any(r.project_id == proj_b.id for r in all_calls)

    # Project A's graph view must reference ONLY test-project permalinks.
    view_a = await svc_a._load_graph_view()

    def _view_permalinks(view) -> set:
        out = set(view.calls_forward.keys())
        for vs in view.calls_forward.values():
            out.update(vs)
        out.update(view.imports.keys())
        for vs in view.imports.values():
            out.update(vs)
        out.update(view.defined_in.keys())
        out.update(view.defined_in.values())
        return out

    perms = _view_permalinks(view_a)
    assert perms, "expected project A's view to contain edges"
    foreign = [p for p in perms if p.startswith("code://other-project/")]
    assert foreign == [], f"cross-project relation leak into A's view: {foreign}"
    assert all(p.startswith("code://test-project/") for p in perms)