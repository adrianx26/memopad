"""Operational retry, concurrency, and watermark persistence checks."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from memopad.services.distillation_scheduler import (
    DistillationScheduler,
    PipelineConfig,
    TRIGGER_L1_DISTILL,
)
from memopad.services.distillation_service import DistillationState


def _pipeline_config(**overrides) -> PipelineConfig:
    options = {
        "every_n_conversations": 1,
        "max_memories_per_session": 8,
        "l1_idle_timeout_seconds": 30,
        "l2_min_interval_seconds": 0,
        "persona_trigger_every_n": 0,
        "enable_warmup": False,
    }
    options.update(overrides)
    return PipelineConfig(**options)


@pytest.mark.asyncio
async def test_failed_trigger_preserves_ingestion_for_retry():
    attempts = 0

    async def fail_once(trigger):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary distillation failure")

    scheduler = DistillationScheduler(_pipeline_config(), callback=fail_once)
    with pytest.raises(RuntimeError, match="temporary"):
        await scheduler.record_new_memory(4)

    state = scheduler.state_for(4)
    assert state.new_memory_count == 1
    assert state.memories_since_persona == 1

    completed = []

    async def succeed(trigger):
        completed.append(trigger)

    scheduler.callback = succeed
    fired = await scheduler.record_new_memory(4)

    assert len(fired) == 1
    assert fired[0].trigger_type == TRIGGER_L1_DISTILL
    assert fired[0].consumed_memories == 2
    assert completed == fired
    assert scheduler.state_for(4).new_memory_count == 0


@pytest.mark.asyncio
async def test_concurrent_events_are_serialized_without_losing_cadence_counts():
    fired = []

    async def callback(trigger):
        await asyncio.sleep(0)
        fired.append(trigger)

    scheduler = DistillationScheduler(
        _pipeline_config(every_n_conversations=4),
        callback=callback,
    )

    await asyncio.gather(*(scheduler.record_new_memory(9) for _ in range(12)))

    assert len(fired) == 3
    assert all(trigger.consumed_memories == 4 for trigger in fired)
    assert scheduler.state_for(9).new_memory_count == 0


@pytest.mark.asyncio
async def test_failed_idle_trigger_restores_pending_work():
    async def fail(trigger):
        raise RuntimeError("idle pass failed")

    scheduler = DistillationScheduler(
        _pipeline_config(every_n_conversations=100),
        callback=fail,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    await scheduler.record_new_memory(2)

    with pytest.raises(RuntimeError, match="idle pass"):
        await scheduler.evaluate_idle_and_dispatch(
            2, now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        )

    assert scheduler.state_for(2).new_memory_count == 1


def test_distillation_state_round_trips_atomically(tmp_path):
    path = tmp_path / "distillation" / "project-state.json"
    state = DistillationState(path)
    value = {"last_l1_at": "2026-01-01T00:00:00+00:00"}

    state.save(value)

    assert json.loads(path.read_text(encoding="utf-8")) == value
    assert state.load() == value
    assert list(path.parent.glob("*.tmp")) == []


def test_corrupt_distillation_state_is_reported(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable"):
        DistillationState(path).load()


def test_non_object_distillation_state_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        DistillationState(path).load()
