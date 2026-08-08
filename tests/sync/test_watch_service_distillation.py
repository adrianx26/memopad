"""Tests for the Tb L0-L3 distillation hook in WatchService.

``WatchService.handle_changes`` nudges the automatic distillation pipeline
(``scheduler.record_new_memory``) at the end of a change batch when
``levels_pipeline_automatic`` is on and the batch actually synced a file. The watch
flow bypasses the API create path, so without this hook distillation stays dormant
during a watch/sync session even though new L0 notes arrived.

These tests isolate the gate + glue from the real scheduler/sync by injecting a
fake ``distillation_scheduler_factory`` (mirrors the ``codegraph_service_factory``
injection in ``test_watch_service_code_reindex``). File sync uses a minimal
``SyncService`` stand-in (the shared ``sync_service`` conftest fixture is currently
out of sync with ``SyncService.__init__`` — pre-existing — so we avoid it).

Cases:
  * trigger fires when the flag is on + a file is synced.
  * trigger does NOT fire when ``levels_pipeline_automatic`` is off.
  * a scheduler build failure degrades explicitly without aborting the file sync.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from watchfiles.main import Change

from memopad.config import MemoPadConfig
from memopad.sync.watch_service import WatchService


class _FakeScheduler:
    """Stand-in DistillationScheduler that records record_new_memory calls."""

    def __init__(self):
        self.recorded: list[int] = []

    async def record_new_memory(self, project_id: int):
        self.recorded.append(project_id)


class _FakeSyncService:
    """Minimal SyncService stand-in for added-only change batches."""

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
    project_repository, *, automatic: bool, scheduler: _FakeScheduler, raise_on_build: bool = False
) -> WatchService:
    async def sync_factory(_project):
        return _FakeSyncService()

    async def sched_factory():
        if raise_on_build:
            raise RuntimeError("scheduler boom")
        return scheduler

    return WatchService(
        MemoPadConfig(levels_enabled=True, levels_pipeline_automatic=automatic),
        project_repository,
        quiet=True,
        sync_service_factory=sync_factory,
        distillation_scheduler_factory=sched_factory,
    )


@pytest.fixture
def registered_project(monkeypatch, test_project):
    """Make handle_changes' deleted-project guard recognize ``test_project``.

    Mirrors the same guard-stubbing in test_watch_service_code_reindex: the guard
    does ``ConfigManager().projects`` and skips the batch when the project name is
    absent. We point ``ConfigManager`` at a stub whose ``.projects`` contains the
    test project so the batch (and the distillation hook) runs.
    """
    from memopad import config as config_module

    class _StubConfigManager:
        def __init__(self):
            self.projects = {test_project.name: test_project.path}

    monkeypatch.setattr(config_module, "ConfigManager", _StubConfigManager)


async def _drain() -> None:
    """Let fire-and-forget asyncio.create_task coroutines get a chance to run."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_distillation_triggered_when_flag_on(
    project_repository, test_project, project_config, registered_project
):
    scheduler = _FakeScheduler()
    watch_service = _make_watch_service(project_repository, automatic=True, scheduler=scheduler)

    note = project_config.home / "note.md"
    note.write_text("# A note\n- [definition] A thing is a thing\n", encoding="utf-8")

    await watch_service.handle_changes(test_project, {(Change.added, str(note))})
    await _drain()

    assert scheduler.recorded == [test_project.id]


@pytest.mark.asyncio
async def test_distillation_not_triggered_when_flag_off(
    project_repository, test_project, project_config, registered_project
):
    scheduler = _FakeScheduler()
    watch_service = _make_watch_service(project_repository, automatic=False, scheduler=scheduler)

    note = project_config.home / "off.md"
    note.write_text("# Off note\n", encoding="utf-8")

    await watch_service.handle_changes(test_project, {(Change.added, str(note))})
    await _drain()

    assert scheduler.recorded == []


@pytest.mark.asyncio
async def test_distillation_hook_degrades_on_build_failure(
    project_repository, test_project, project_config, registered_project
):
    scheduler = _FakeScheduler()
    # raise_on_build=True -> the factory raises; the hook must log+degrade, not abort.
    watch_service = _make_watch_service(
        project_repository, automatic=True, scheduler=scheduler, raise_on_build=True
    )

    note = project_config.home / "boom.md"
    note.write_text("# Boom note\n", encoding="utf-8")

    # Must not raise — file sync completes and the hook degrades.
    await watch_service.handle_changes(test_project, {(Change.added, str(note))})
    await _drain()

    assert scheduler.recorded == []