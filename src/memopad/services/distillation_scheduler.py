"""Reactive distillation scheduler (Tb G3).

The levels plan (§7.3) lists only *manual* distillation triggers (CLI / sync /
cron). Tb adds **event-driven cadences with validated numeric parameters**,
an **idle timeout**, and **warmup** — turning distillation from a batch job into a
reactive process. This module is that reactive core.

Scope note
----------
The actual distiller (L0 -> L1 -> L2 -> L3) is not yet implemented (it is Faza 1/4
of `memopad-levels-implementation-plan.md`). So this scheduler does **not** perform
distillation. It is a pure *trigger-policy engine*: given ingestion events and
timing, it decides *when* each level should be distilled and emits a
`DistillationTrigger`. The actual work is delegated to a `DistillationCallback`
(default: no-op) — the seam where the future distiller plugs in.

This keeps the feature non-breaking: with `levels_pipeline_automatic` off (the
default) nothing is wired into the create/sync hot path, and even when on, firing
a trigger only calls the (no-op) callback. All cadences are configurable; 0 means
that individual trigger is disabled.

Design
------
- Per-project in-memory state (counters, last-fired timestamps, warmup cursor).
- A pluggable `clock` callable (returns a datetime) so tests are deterministic
  without sleeping — `datetime.now()` is never called directly inside the engine.
- Policy methods are pure functions of (state, config, now); `record_new_memory`
  is the single async entry point that updates state, evaluates policy, and
  dispatches fired triggers to the callback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from memopad.config import MemoPadConfig

# --- Trigger types --------------------------------------------------------

TRIGGER_L1_DISTILL: str = "l1_distill"
TRIGGER_L2_SCENARIO: str = "l2_scenario"
TRIGGER_L3_PERSONA: str = "l3_persona"
TRIGGER_WARMUP: str = "warmup"


@dataclass
class DistillationTrigger:
    """A policy decision that a distillation pass should run.

    `target_level` is L1/L2/L3 (warmup carries L0 — it widens retrieval, not
    distils). `max_memories` caps how many raw memories the pass may consume
    (the anti-pollution guard). `depth` is the warmup retrieval breadth.
    """

    trigger_type: str
    project_id: int
    reason: str
    target_level: str
    max_memories: int
    fired_at: datetime
    depth: Optional[int] = None


# --- Config projection ----------------------------------------------------


@dataclass
class PipelineConfig:
    """Numeric cadences projected from `MemoPadConfig` (Tb G3)."""

    every_n_conversations: int
    max_memories_per_session: int
    l1_idle_timeout_seconds: int
    l2_min_interval_seconds: int
    persona_trigger_every_n: int
    enable_warmup: bool

    @classmethod
    def from_app_config(cls, cfg: MemoPadConfig) -> "PipelineConfig":
        return cls(
            every_n_conversations=cfg.pipeline_every_n_conversations,
            max_memories_per_session=cfg.pipeline_max_memories_per_session,
            l1_idle_timeout_seconds=cfg.pipeline_l1_idle_timeout_seconds,
            l2_min_interval_seconds=cfg.pipeline_l2_min_interval_seconds,
            persona_trigger_every_n=cfg.pipeline_persona_trigger_every_n,
            enable_warmup=cfg.pipeline_enable_warmup,
        )

    @property
    def has_any_trigger(self) -> bool:
        """True if at least one cadence is active (non-zero)."""
        return (
            self.every_n_conversations > 0
            or self.l1_idle_timeout_seconds > 0
            or self.l2_min_interval_seconds > 0
            or self.persona_trigger_every_n > 0
            or self.enable_warmup
        )


def is_pipeline_active(app_config: MemoPadConfig) -> bool:
    """True if the reactive pipeline should run.

    Both `levels_enabled` (the L0–L3 convention) and `levels_pipeline_automatic`
    (the reactive scheduler) must be on. Centralized so the integration hook and
    callers share one definition of "active".
    """
    return bool(app_config.levels_enabled and app_config.levels_pipeline_automatic)


# --- Callback seam --------------------------------------------------------


class DistillationCallback(Protocol):
    """The seam where the future distiller plugs in.

    The scheduler calls it with each fired trigger. The default implementation is
    a no-op, so wiring the scheduler into the create path cannot perform any
    distillation work until a real callback is registered.
    """

    async def __call__(self, trigger: DistillationTrigger) -> None: ...


async def _no_op_callback(trigger: DistillationTrigger) -> None:
    """Default callback: record nothing, do nothing (no-op seam)."""
    return None


# --- Per-project state ----------------------------------------------------


@dataclass
class ProjectPipelineState:
    """In-memory bookkeeping for one project's distillation cadences."""

    new_memory_count: int = 0  # memories ingested since the last L1 pass
    memories_since_persona: int = 0  # memories ingested since the last L3 pass
    last_l1_at: Optional[datetime] = None
    last_l2_at: Optional[datetime] = None
    last_persona_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    # Warmup: index into the doubling sequence [1,2,4,8,...] of the next threshold
    # to cross. Starts at 0 (threshold 1).
    warmup_next_idx: int = 0
    warmup_emitted: List[int] = field(default_factory=list)


# Doubling warmup thresholds, capped so the sequence can't grow unbounded.
# 1 -> 2 -> 4 -> 8 -> 16 -> 32 -> 64 -> 128. Beyond 128 retrieval stays wide.
_WARMUP_THRESHOLDS: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128)


def warmup_thresholds() -> tuple[int, ...]:
    """The doubling warmup sequence (1 -> 2 -> 4 -> ... -> 128)."""
    return _WARMUP_THRESHOLDS


# --- The scheduler --------------------------------------------------------


# A clock returns the current datetime. Default uses timezone-aware UTC. Injected
# in tests so the engine never calls datetime.now() directly.
Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class DistillationScheduler:
    """Event-driven trigger-policy engine for the L0–L3 distillation pipeline.

    Stateless across processes (in-memory only); the cadences it enforces are the
    real value — they encode *when* to distil, which is what Tb contributes
    over the manual-only plan. The engine is agnostic to how events arrive
    (MCP session, sync watcher, CLI); callers feed it via `record_new_memory` /
    `evaluate_idle`.
    """

    def __init__(
        self,
        pipeline_config: PipelineConfig,
        *,
        callback: Optional[DistillationCallback] = None,
        clock: Optional[Clock] = None,
    ):
        self.config = pipeline_config
        self.callback: DistillationCallback = callback or _no_op_callback  # type: ignore[assignment]
        self.clock: Clock = clock or _default_clock
        self._state: Dict[int, ProjectPipelineState] = {}

    # --- state access ---

    def state_for(self, project_id: int) -> ProjectPipelineState:
        return self._state.setdefault(project_id, ProjectPipelineState())

    def reset(self, project_id: int) -> None:
        """Clear bookkeeping for a project (e.g. on session end)."""
        self._state.pop(project_id, None)

    # --- event entry points ---

    async def record_new_memory(self, project_id: int, *, now: Optional[datetime] = None) -> List[DistillationTrigger]:
        """Record a new memory ingestion and fire any due triggers.

        This is the primary hook (called by the future create/sync integration
        when `levels_pipeline_automatic` is on). Returns the triggers that fired,
        in order, and dispatches each to the callback.
        """
        if not self.config.has_any_trigger:
            return []

        ts = now if now is not None else self.clock()
        state = self.state_for(project_id)
        state.new_memory_count += 1
        state.memories_since_persona += 1
        state.last_activity_at = ts

        fired: List[DistillationTrigger] = []

        # Warmup widens retrieval as the session grows (1 -> 2 -> 4 -> ...).
        warmup = self._maybe_warmup(project_id, state, ts)
        if warmup:
            fired.append(warmup)

        # L3 persona: regenerate after every N new memories (heaviest, so first
        # to check conceptually but emitted after warmup for ordering clarity).
        persona = self._maybe_persona(project_id, state, ts)
        if persona:
            fired.append(persona)

        # L1 distill: every N new memories, capped per pass.
        l1 = self._maybe_l1(project_id, state, ts)
        if l1:
            fired.append(l1)

        # Dispatch to the callback seam (no-op by default).
        for trig in fired:
            await self.callback(trig)
        return fired

    def evaluate_idle(self, project_id: int, *, now: Optional[datetime] = None) -> Optional[DistillationTrigger]:
        """If the project has been idle past `l1_idle_timeout`, fire an L1 pass.

        Called by a periodic tick / watcher heartbeat. Does NOT dispatch to the
        callback (the caller decides); returns the trigger for the caller to fire
        via `dispatch`. Idle only fires if there are pending new memories to distil
        — an idle project with nothing queued has nothing to do.
        """
        timeout = self.config.l1_idle_timeout_seconds
        if timeout <= 0:
            return None
        ts = now if now is not None else self.clock()
        state = self._state.get(project_id)
        if not state or state.last_activity_at is None:
            return None
        if state.new_memory_count <= 0:
            return None  # nothing pending to distil
        if (ts - state.last_activity_at) < timedelta(seconds=timeout):
            return None
        trig = DistillationTrigger(
            trigger_type=TRIGGER_L1_DISTILL,
            project_id=project_id,
            reason=f"idle {timeout}s with {state.new_memory_count} pending memories",
            target_level="L1",
            max_memories=self.config.max_memories_per_session,
            fired_at=ts,
        )
        # An idle pass consumes the pending queue.
        state.new_memory_count = 0
        state.last_l1_at = ts
        return trig

    async def dispatch(self, trigger: DistillationTrigger) -> None:
        """Fire a single trigger through the callback seam."""
        await self.callback(trigger)

    def should_trigger_l2(self, project_id: int, *, now: Optional[datetime] = None) -> bool:
        """L2 (scenario) debounce gate: True if enough time has elapsed since the last L2.

        Returns True when L2 distillation is permitted by the min-interval policy.
        Callers still need their own readiness criteria (e.g. enough L1 facts);
        this only answers *when* it is allowed.
        """
        ts = now if now is not None else self.clock()
        state = self._state.get(project_id)
        if state is None or state.last_l2_at is None:
            return True  # never run → permitted
        if self.config.l2_min_interval_seconds <= 0:
            return True  # no debounce configured
        return (ts - state.last_l2_at) >= timedelta(seconds=self.config.l2_min_interval_seconds)

    def mark_l2_fired(self, project_id: int, *, now: Optional[datetime] = None) -> None:
        """Record that an L2 pass ran (updates the debounce watermark)."""
        ts = now if now is not None else self.clock()
        state = self.state_for(project_id)
        state.last_l2_at = ts

    # --- policy internals ---

    def _maybe_warmup(
        self, project_id: int, state: ProjectPipelineState, ts: datetime
    ) -> Optional[DistillationTrigger]:
        if not self.config.enable_warmup:
            return None
        thresholds = warmup_thresholds()
        if state.warmup_next_idx >= len(thresholds):
            return None  # already at max width
        next_threshold = thresholds[state.warmup_next_idx]
        if state.new_memory_count < next_threshold:
            return None
        # Crossed the threshold: widen retrieval to this depth and advance.
        state.warmup_next_idx += 1
        state.warmup_emitted.append(next_threshold)
        return DistillationTrigger(
            trigger_type=TRIGGER_WARMUP,
            project_id=project_id,
            reason=f"warmup depth {next_threshold} (session grew to {state.new_memory_count})",
            target_level="L0",
            max_memories=self.config.max_memories_per_session,
            fired_at=ts,
            depth=next_threshold,
        )

    def _maybe_persona(
        self, project_id: int, state: ProjectPipelineState, ts: datetime
    ) -> Optional[DistillationTrigger]:
        n = self.config.persona_trigger_every_n
        if n <= 0:
            return None
        if state.memories_since_persona < n:
            return None
        state.memories_since_persona = 0
        state.last_persona_at = ts
        return DistillationTrigger(
            trigger_type=TRIGGER_L3_PERSONA,
            project_id=project_id,
            reason=f"{n} new memories since last persona",
            target_level="L3",
            max_memories=self.config.max_memories_per_session,
            fired_at=ts,
        )

    def _maybe_l1(
        self, project_id: int, state: ProjectPipelineState, ts: datetime
    ) -> Optional[DistillationTrigger]:
        n = self.config.every_n_conversations
        if n <= 0:
            return None
        if state.new_memory_count < n:
            return None
        # Consume the batch: reset the counter and stamp the watermark.
        consumed = state.new_memory_count
        state.new_memory_count = 0
        state.last_l1_at = ts
        return DistillationTrigger(
            trigger_type=TRIGGER_L1_DISTILL,
            project_id=project_id,
            reason=f"{consumed} new memories reached the every-{n} cadence",
            target_level="L1",
            max_memories=self.config.max_memories_per_session,
            fired_at=ts,
        )