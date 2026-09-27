"""Tests for non-blocking file-count subprocess handling."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from memopad.sync import sync_service as sync_service_module
from memopad.sync.sync_service import SyncService


@pytest.mark.asyncio
async def test_quick_count_runs_find_in_a_worker_thread(monkeypatch, tmp_path: Path):
    calls = {}

    async def fake_to_thread(function, *args, **kwargs):
        calls["function"] = function
        calls["args"] = args
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=b"/a\n/b\n", stderr=b"")

    monkeypatch.setattr(sync_service_module.sys, "platform", "linux")
    monkeypatch.setattr(sync_service_module.asyncio, "to_thread", fake_to_thread)
    service = SyncService.__new__(SyncService)

    count = await service._quick_count_files(tmp_path)

    assert count == 2
    assert calls == {
        "function": subprocess.run,
        "args": (["find", str(tmp_path), "-type", "f"],),
        "kwargs": {"capture_output": True, "check": False},
    }


@pytest.mark.asyncio
async def test_quick_count_uses_async_scanner_on_windows(monkeypatch, tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "note.md").write_text("note", encoding="utf-8")
    service = SyncService.__new__(SyncService)
    service._ignore_patterns = []

    def fail_if_run(*args, **kwargs):
        pytest.fail("Windows file counting must not spawn an asyncio subprocess")

    monkeypatch.setattr(sync_service_module.sys, "platform", "win32")
    monkeypatch.setattr(sync_service_module.subprocess, "run", fail_if_run)

    assert await service._quick_count_files(tmp_path) == 1


@pytest.mark.asyncio
async def test_quick_count_falls_back_when_find_fails(monkeypatch, tmp_path: Path):
    (tmp_path / "note.md").write_text("note", encoding="utf-8")
    service = SyncService.__new__(SyncService)
    service._ignore_patterns = []

    async def failed_find(function, *args, **kwargs):
        return SimpleNamespace(returncode=1, stderr=b"find failed", stdout=b"")

    monkeypatch.setattr(sync_service_module.sys, "platform", "linux")
    monkeypatch.setattr(sync_service_module.asyncio, "to_thread", failed_find)

    assert await service._quick_count_files(tmp_path) == 1


@pytest.mark.asyncio
async def test_quick_count_falls_back_when_find_is_unavailable(monkeypatch, tmp_path: Path):
    (tmp_path / "note.md").write_text("note", encoding="utf-8")
    service = SyncService.__new__(SyncService)
    service._ignore_patterns = []

    async def missing_find(function, *args, **kwargs):
        raise FileNotFoundError("find")

    monkeypatch.setattr(sync_service_module.sys, "platform", "linux")
    monkeypatch.setattr(sync_service_module.asyncio, "to_thread", missing_find)

    assert await service._quick_count_files(tmp_path) == 1
