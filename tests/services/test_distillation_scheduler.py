"""Tests for the reactive distillation scheduler (Tb G3).

The scheduler is a pure trigger-policy engine with an injected clock, so these
tests are deterministic with no sleeping. They cover the cadences, idle timeout,
warmup doubling, debounce, the no-op callback seam, and the opt-in gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memopad.config import MemoPadConfig
from memopad.services.distillation_scheduler import (
    PipelineConfig,
    ProjectPipelineState,
    TRIGGER_L1_DISTILL,
    TRIGGER_L3_PERSONA,
    TRIGGER_WARMUP,
    DistillationScheduler,
    DistillationTrigger,
    is_pipeline_active,
    warmup_thresholds,
)


def _cfg(**overrides) -> PipelineConfig:
    base = dict(
        every_n_conversations=5,
        max_memories_per_session=20,
        l1_idle_timeout_seconds=600,
        l2_min_interval_seconds=900,
        persona_trigger_every_n=50,
        enable_warmup=True,
    )
    base.update(overrides)
    return PipelineConfig(**base)


def _clock(start: datetime):
    """Return a callable clock whose time advances by `step` on each call."""
    state = {"t": start}

    def _now() -> datetime:
        return state["t"]

    def _advance(delta: timedelta) -> datetime:
        state["t"] = state["t"] + delta
        return state["t"]

    return _now, _advance


class _Collector:
    """Async callback that records every fired trigger."""

    def __init__(self):
        self.fired: list[DistillationTrigger] = []

    async def __call__(self, trigger: DistillationTrigger) -> None:
        self.fired.append(trigger)


@pytest.mark.asyncio
async def test_l1_fires_every_n_and_resets():
    cfg = _cfg(every_n_conversations=3, persona_trigger_every_n=0, enable_warmup=False)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, _ = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)
    col = _Collector()
    sched.callback = col  # type: ignore[assignment]

    # 1, 2 → nothing; 3 → L1 fires.
    for _ in range(2):
        await sched.record_new_memory(1)
    assert not col.fired
    await sched.record_new_memory(1)
    l1 = [t for t in col.fired if t.trigger_type == TRIGGER_L1_DISTILL]
    assert len(l1) == 1
    assert l1[0].target_level == "L1"
    assert l1[0].max_memories == 20

    # Counter reset: 2 more → nothing; 3rd → fires again.
    col.fired.clear()
    for _ in range(2):
        await sched.record_new_memory(1)
    assert not [t for t in col.fired if t.trigger_type == TRIGGER_L1_DISTILL]
    await sched.record_new_memory(1)
    assert len([t for t in col.fired if t.trigger_type == TRIGGER_L1_DISTILL]) == 1


@pytest.mark.asyncio
async def test_persona_fires_every_n_and_resets():
    cfg = _cfg(every_n_conversations=0, persona_trigger_every_n=4, enable_warmup=False)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, _ = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)
    col = _Collector()
    sched.callback = col  # type: ignore[assignment]

    for _ in range(3):
        await sched.record_new_memory(1)
    assert not [t for t in col.fired if t.trigger_type == TRIGGER_L3_PERSONA]
    await sched.record_new_memory(1)
    personas = [t for t in col.fired if t.trigger_type == TRIGGER_L3_PERSONA]
    assert len(personas) == 1
    assert personas[0].target_level == "L3"


@pytest.mark.asyncio
async def test_warmup_doubling_sequence():
    cfg = _cfg(every_n_conversations=0, persona_trigger_every_n=0, enable_warmup=True)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, _ = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)
    col = _Collector()
    sched.callback = col  # type: ignore[assignment]

    # Thresholds are 1, 2, 4, 8, ... — one warmup trigger emitted as each is crossed.
    expected = warmup_thresholds()
    seen: list[int] = []
    for _ in range(8):
        await sched.record_new_memory(1)
    seen = [t.depth for t in col.fired if t.trigger_type == TRIGGER_WARMUP]
    # At counts 1,2,4,8 → depths 1,2,4,8.
    assert seen == [1, 2, 4, 8]
    # State cursor advanced past index 3 (threshold 8).
    assert sched.state_for(1).warmup_next_idx == 4


@pytest.mark.asyncio
async def test_warmup_disabled():
    cfg = _cfg(every_n_conversations=0, persona_trigger_every_n=0, enable_warmup=False)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, _ = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)
    for _ in range(10):
        fired = await sched.record_new_memory(1)
    assert not [t for t in fired if t.trigger_type == TRIGGER_WARMUP]


@pytest.mark.asyncio
async def test_idle_timeout_fires_l1_only_when_pending():
    cfg = _cfg(every_n_conversations=100, l1_idle_timeout_seconds=60, enable_warmup=False, persona_trigger_every_n=0)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, advance = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)

    # Ingest one memory (pending), then go idle past the timeout.
    await sched.record_new_memory(1)
    advance(timedelta(seconds=70))
    trig = sched.evaluate_idle(1)
    assert trig is not None
    assert trig.trigger_type == TRIGGER_L1_DISTILL
    assert "idle" in trig.reason
    # Idle pass consumed the pending queue.
    assert sched.state_for(1).new_memory_count == 0

    # Now nothing pending → idle evaluation returns None.
    advance(timedelta(seconds=70))
    assert sched.evaluate_idle(1) is None


def test_idle_disabled_when_timeout_zero():
    cfg = _cfg(l1_idle_timeout_seconds=0)
    sched = DistillationScheduler(cfg)
    assert sched.evaluate_idle(1) is None


def test_l2_debounce():
    cfg = _cfg(l2_min_interval_seconds=100, enable_warmup=False)
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    now, advance = _clock(start)
    sched = DistillationScheduler(cfg, clock=now)

    # Never run → permitted.
    assert sched.should_trigger_l2(1) is True
    sched.mark_l2_fired(1)
    # Immediately after → not permitted (within 100s).
    assert sched.should_trigger_l2(1) is False
    advance(timedelta(seconds=99))
    assert sched.should_trigger_l2(1) is False
    advance(timedelta(seconds=2))
    assert sched.should_trigger_l2(1) is True


def test_l2_no_debounce_when_interval_zero():
    cfg = _cfg(l2_min_interval_seconds=0)
    sched = DistillationScheduler(cfg)
    sched.mark_l2_fired(1)
    assert sched.should_trigger_l2(1) is True


@pytest.mark.asyncio
async def test_noop_when_all_cadences_zero():
    cfg = PipelineConfig(
        every_n_conversations=0,
        max_memories_per_session=20,
        l1_idle_timeout_seconds=0,
        l2_min_interval_seconds=0,
        persona_trigger_every_n=0,
        enable_warmup=False,
    )
    sched = DistillationScheduler(cfg)
    fired = await sched.record_new_memory(1)
    assert fired == []
    assert cfg.has_any_trigger is False


@pytest.mark.asyncio
async def test_callback_seam_is_invoked():
    cfg = _cfg(every_n_conversations=1, persona_trigger_every_n=0, enable_warmup=False)
    sched = DistillationScheduler(cfg)
    col = _Collector()
    sched.callback = col  # type: ignore[assignment]
    fired = await sched.record_new_memory(1)
    assert len(fired) == 1
    assert col.fired == fired  # callback received the same triggers


@pytest.mark.asyncio
async def test_default_callback_is_noop():
    cfg = _cfg(every_n_conversations=1, persona_trigger_every_n=0, enable_warmup=False)
    sched = DistillationScheduler(cfg)  # no callback → no-op seam
    # Must not raise and must still return the trigger.
    fired = await sched.record_new_memory(1)
    assert len(fired) == 1


def test_is_pipeline_active_requires_both_flags():
    def _mc(**ov) -> MemoPadConfig:
        c = MemoPadConfig()
        for k, v in ov.items():
            setattr(c, k, v)
        return c

    assert is_pipeline_active(_mc(levels_enabled=True, levels_pipeline_automatic=True)) is True
    assert is_pipeline_active(_mc(levels_enabled=True, levels_pipeline_automatic=False)) is False
    assert is_pipeline_active(_mc(levels_enabled=False, levels_pipeline_automatic=True)) is False


def test_pipeline_config_from_app_config():
    c = MemoPadConfig()
    pc = PipelineConfig.from_app_config(c)
    assert pc.every_n_conversations == c.pipeline_every_n_conversations
    assert pc.max_memories_per_session == c.pipeline_max_memories_per_session
    assert pc.enable_warmup is c.pipeline_enable_warmup


def test_reset_clears_state():
    cfg = _cfg()
    sched = DistillationScheduler(cfg)
    sched.state_for(1).new_memory_count = 5
    sched.reset(1)
    assert sched.state_for(1).new_memory_count == 0  # fresh state