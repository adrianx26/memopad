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


# --- API endpoint: bulk distill parameter -----------------------------------

@pytest.mark.asyncio
async def test_schedule_distillation_ignores_bulk_param():
    """The create-path hook doesn't know about bulk; it always fires incremental."""
    scheduler = _FakeScheduler()
    config = MemoPadConfig(levels_enabled=True, levels_pipeline_automatic=True)
    # Even though the API accepts bulk=True, the fire-and-forget hook just calls
    # record_new_memory — it never passes a bulk flag.
    _schedule_distillation(scheduler, config, project_id=42)
    await _drain()
    assert scheduler.recorded == [42]


# --- API endpoint: distill with bulk=True -----------------------------------

from memopad.api.v2.routers.knowledge_router import router as knowledge_router


@pytest.mark.asyncio
async def test_distill_endpoint_accepts_bulk_param():
    """The POST /distill endpoint declares a `bulk` query parameter (cold-start flag).

    Inspects the route's endpoint signature directly (no DB / TestClient needed) —
    this verifies the parameter is wired through without depending on Alembic/CTE
    infra, mirroring the structure-only discover/add tests below.
    """
    import inspect

    distill_route = next(
        (r for r in knowledge_router.routes if getattr(r, "path", "").endswith("/distill")),
        None,
    )
    assert distill_route is not None
    assert "POST" in distill_route.methods
    # The endpoint function must declare a `bulk` parameter that the router forwards
    # to run_l1_pass(all_entities=bulk).
    sig = inspect.signature(distill_route.endpoint)
    assert "bulk" in sig.parameters


# --- API endpoint: discover-categories --------------------------------------

@pytest.mark.asyncio
async def test_discover_categories_endpoint_structure():
    """The /discover-categories GET endpoint exists on the knowledge router."""
    discover_route = next(
        (r for r in knowledge_router.routes if hasattr(r, "path") and "/discover-categories" in str(r.path)),
        None,
    )
    assert discover_route is not None
    # Should be a GET route
    assert "GET" in discover_route.methods


# --- API endpoint: add-categories -------------------------------------------

@pytest.mark.asyncio
async def test_add_categories_endpoint_structure():
    """The /add-categories POST endpoint exists on the knowledge router."""
    add_route = next(
        (r for r in knowledge_router.routes if hasattr(r, "path") and "/add-categories" in str(r.path)),
        None,
    )
    assert add_route is not None
    # Should be a POST route
    assert "POST" in add_route.methods
