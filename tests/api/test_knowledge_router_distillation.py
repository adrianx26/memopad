"""Tests for the create-path distillation trigger in the v2 knowledge router.

``_schedule_distillation`` is the fire-and-forget hook the create/update/edit
endpoints call after a successful write. It gates on ``is_pipeline_active``
(``levels_enabled`` AND ``levels_pipeline_automatic``) and nudges the scheduler.
These tests exercise the helper directly (no TestClient / DB) so the create-path
wiring is verified without touching the pre-existing Alembic/CTE infra failures.
"""

from __future__ import annotations

import asyncio

import pytest

from memopad.api.v2.routers.knowledge_router import _schedule_distillation
from memopad.config import MemoPadConfig


class _FakeScheduler:
    def __init__(self):
        self.recorded: list[int] = []

    async def record_new_memory(self, project_id: int):
        self.recorded.append(project_id)


async def _drain() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_schedule_distillation_fires_when_pipeline_active():
    scheduler = _FakeScheduler()
    config = MemoPadConfig(levels_enabled=True, levels_pipeline_automatic=True)
    _schedule_distillation(scheduler, config, project_id=42)
    await _drain()
    assert scheduler.recorded == [42]


@pytest.mark.asyncio
async def test_schedule_distillation_skips_when_automatic_off():
    scheduler = _FakeScheduler()
    config = MemoPadConfig(levels_enabled=True, levels_pipeline_automatic=False)
    _schedule_distillation(scheduler, config, project_id=42)
    await _drain()
    assert scheduler.recorded == []


@pytest.mark.asyncio
async def test_schedule_distillation_skips_when_levels_disabled():
    scheduler = _FakeScheduler()
    config = MemoPadConfig(levels_enabled=False, levels_pipeline_automatic=True)
    _schedule_distillation(scheduler, config, project_id=42)
    await _drain()
    assert scheduler.recorded == []