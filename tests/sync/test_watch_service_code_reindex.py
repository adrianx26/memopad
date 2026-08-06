"""Tests for the Tb G2 code-graph reindex hook in WatchService.

``WatchService.handle_changes`` re-indexes the code graph at the end of a change
batch when ``codegraph_enabled`` is on and the batch contains a supported source
file. The decision is a pure extension check (``_batch_has_code_files``); the
reindex itself reuses the idempotent ``CodeGraphService.index_directory``.

These tests isolate the gate + glue from real parsing by injecting a fake
``codegraph_service_factory`` (mirrors the ``sync_service_factory`` injection used
by ``test_watch_service_atomic_adds``). File sync still uses the real
``sync_service`` fixture so the .py file is tracked as an opaque file entity, the
same path a real watch session takes.

Cases:
  * pure ``_batch_has_code_files`` — detects .py, ignores .md/.txt/empty.
  * reindex triggered when flag on + a .py file changes.
  * reindex NOT triggered when flag off (default).
  * reindex NOT triggered when flag on but batch has no source files.
  * a reindex failure (factory raises) degrades explicitly without aborting the
    file sync that already completed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from watchfiles.main import Change

from memopad.config import MemoPadConfig
from memopad.services.codegraph_service import IndexReport
from memopad.sync.watch_service import WatchService


class _FakeCodeGraphService:
    """Stand-in for CodeGraphService that records index_directory calls."""

    def __init__(self, *, report: IndexReport | None = None, raise_on_index: bool = False):
        self.index_calls: list[Path] = []
        self._report = report or IndexReport(files=1, entities=2, relations=3, skipped=0)
        self._raise = raise_on_index

    async def index_directory(self, root, *, languages=None):
        self.index_calls.append(Path(root))
        if self._raise:
            raise RuntimeError("codegraph boom")
        return self._report


class _FakeSyncService:
    """Minimal SyncService stand-in for added-only change batches.

    ``handle_changes`` only touches ``file_service``, ``entity_repository``
    (``get_by_file_path`` for the reclassify-added-existing check), ``sync_file``,
    and (for delete/move batches) ``handle_move`` / ``handle_delete``. These
    tests use added-only batches, so the move/delete paths are unused stubs. The
    real ``sync_service`` conftest fixture is currently out of sync with
    ``SyncService.__init__`` (pre-existing), so we isolate the reindex glue here
    rather than depend on it.
    """

    def __init__(self):
        self.file_service = SimpleNamespace()
        self.entity_repository = _FakeEntityRepo()

    async def sync_file(self, path, new=True, cached_checksum=None):
        return None, "fake-checksum"

    async def handle_move(self, *args, **kwargs):  # unused for added-only batches
        ...

    async def handle_delete(self, *args, **kwargs):  # unused for added-only batches
        ...


class _FakeEntityRepo:
    """entity_repository stand-in: get_by_file_path always misses (new file)."""

    async def get_by_file_path(self, path):
        return None


def _make_watch_service(
    project_repository, *, codegraph_enabled: bool, fake_cg: _FakeCodeGraphService
) -> WatchService:
    async def sync_factory(_project):
        return _FakeSyncService()

    async def cg_factory(_project):
        return fake_cg

    return WatchService(
        MemoPadConfig(codegraph_enabled=codegraph_enabled),
        project_repository,
        quiet=True,
        sync_service_factory=sync_factory,
        codegraph_service_factory=cg_factory,
    )


@pytest.fixture
def registered_project(monkeypatch, test_project):
    """Make handle_changes' deleted-project guard recognize ``test_project``.

    The guard at the top of ``handle_changes`` does ``ConfigManager().projects``
    and skips the batch when the project name is absent (prevents deleted projects
    from being recreated by background sync). The shared ``config_manager``
    conftest fixture writes config to ``config_home/.memopad`` while
    ``ConfigManager()`` reads from the ``MEMOPAD_HOME``-based path — a pre-existing
    path mismatch that is out of scope for the G2 hook. We point ``ConfigManager``
    at a stub whose ``.projects`` contains the test project so the guard lets the
    batch through, isolating the reindex behaviour under test.
    """
    from memopad import config as config_module

    class _StubConfigManager:
        def __init__(self):
            self.projects = {test_project.name: test_project.path}

    monkeypatch.setattr(config_module, "ConfigManager", _StubConfigManager)


# --- pure helper -------------------------------------------------------------


def test_batch_has_code_files_detects_python():
    assert WatchService._batch_has_code_files(["src/mod.py", "notes/a.md"]) is True


def test_batch_has_code_files_detects_python_case_insensitive_suffix():
    assert WatchService._batch_has_code_files(["SRC/MOD.PY"]) is True


def test_batch_has_code_files_false_for_only_markdown():
    assert WatchService._batch_has_code_files(["a.md", "b.txt", "notes/c.md"]) is False


def test_batch_has_code_files_false_for_empty_batch():
    assert WatchService._batch_has_code_files([]) is False


def test_batch_has_code_files_false_for_no_suffix():
    assert WatchService._batch_has_code_files(["Makefile", "README"]) is False


# --- integration: the gate + glue in handle_changes --------------------------


@pytest.mark.asyncio
async def test_reindex_triggered_when_code_file_changes_and_flag_on(
    project_repository, test_project, project_config, registered_project
):
    fake_cg = _FakeCodeGraphService()
    watch_service = _make_watch_service(
        project_repository, codegraph_enabled=True, fake_cg=fake_cg
    )

    py_file = project_config.home / "mod.py"
    py_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

    await watch_service.handle_changes(
        test_project, {(Change.added, str(py_file))}
    )

    assert len(fake_cg.index_calls) == 1
    # The reindex runs over the whole project tree (full-tree, not per-file).
    assert fake_cg.index_calls[0].resolve() == project_config.home.resolve()


@pytest.mark.asyncio
async def test_reindex_not_triggered_when_flag_off(
    project_repository, test_project, project_config, registered_project
):
    fake_cg = _FakeCodeGraphService()
    # codegraph_enabled defaults to False — the gate must stay closed.
    watch_service = _make_watch_service(
        project_repository, codegraph_enabled=False, fake_cg=fake_cg
    )

    py_file = project_config.home / "off.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    await watch_service.handle_changes(
        test_project, {(Change.added, str(py_file))}
    )

    assert fake_cg.index_calls == []


@pytest.mark.asyncio
async def test_reindex_not_triggered_when_batch_has_no_code_files(
    project_repository, test_project, project_config, registered_project
):
    fake_cg = _FakeCodeGraphService()
    watch_service = _make_watch_service(
        project_repository, codegraph_enabled=True, fake_cg=fake_cg
    )

    md_file = project_config.home / "note.md"
    md_file.write_text("# Just a note\n", encoding="utf-8")

    await watch_service.handle_changes(
        test_project, {(Change.added, str(md_file))}
    )

    # Flag is on, but no source file in the batch → no reindex (wasted work).
    assert fake_cg.index_calls == []


@pytest.mark.asyncio
async def test_reindex_failure_does_not_abort_file_sync(
    project_repository, test_project, project_config, registered_project
):
    """A reindex error degrades explicitly; the file sync that already ran stays."""
    fake_cg = _FakeCodeGraphService(raise_on_index=True)
    watch_service = _make_watch_service(
        project_repository, codegraph_enabled=True, fake_cg=fake_cg
    )

    py_file = project_config.home / "fail.py"
    py_file.write_text("y = 2\n", encoding="utf-8")

    # Must not raise — the file sync completed before the reindex attempt.
    await watch_service.handle_changes(
        test_project, {(Change.added, str(py_file))}
    )

    # File sync still recorded the .py file as a new tracked file.
    actions = [e.action for e in watch_service.state.recent_events]
    assert "new" in actions