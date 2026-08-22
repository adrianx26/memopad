"""Regression guard for the background-write concurrency unification (BUG 6/7
root-cause fix, 0.21.5).

Before 0.21.5 the server ran background writers under TWO uncoordinated budgets
plus one UNBOUNDED path, all serializing on the single SQLite WAL write lock:

  * ``sync_service.sync``        — a per-call ``asyncio.Semaphore(MAX_CONCURRENT_SYNCS)``
  * reindex/distillation tasks   — the process-wide ``get_background_task_semaphore``
  * watch-triggered distillation — a raw ``asyncio.create_task(record_new_memory)``
                                   with NO semaphore at all (the thundering herd)

A CLI ``distill --bulk`` writing many fact files made the watcher fire one
distillation pass per batch with no bound; combined with the separate sync budget
this starved the write lock past ``busy_timeout`` and surfaced as
``database is locked``. 0.21.5 collapses every background writer onto the ONE
shared ``get_background_task_semaphore``.

These are structural assertions (like ``test_search_repository_base_writes_are_bulk_retry_wrapped``)
so the unification cannot be silently reverted: the two paths that previously
bypassed the shared budget must keep referencing it.
"""

import inspect

from memopad.sync.sync_service import SyncService
from memopad.sync.watch_service import WatchService


def test_sync_uses_shared_background_semaphore():
    """sync_service.sync must gate parallel file syncs on the process-wide
    background-task semaphore, not a fresh per-call asyncio.Semaphore."""
    src = inspect.getsource(SyncService.sync)
    assert "get_background_task_semaphore" in src, (
        "sync_service.sync no longer uses the shared background-task semaphore; "
        "a per-call semaphore would re-introduce the dual-budget lock starvation "
        "(BUG 6/7)."
    )
    # The per-call budget must be gone — a new local Semaphore for the sync batch
    # is exactly the regression we are guarding against.
    assert "asyncio.Semaphore(MAX_CONCURRENT_SYNCS)" not in src, (
        "sync_service.sync re-introduced a local MAX_CONCURRENT_SYNCS semaphore."
    )


def test_watch_distillation_is_bounded_by_shared_semaphore():
    """The watch path's fire-and-forget distillation trigger must run under the
    shared background-task semaphore (wrapped in `async with semaphore`), not a
    bare unbounded create_task(record_new_memory)."""
    src = inspect.getsource(WatchService.handle_changes)
    assert "get_background_task_semaphore" in src, (
        "watch_service.handle_changes no longer references the shared background "
        "semaphore for its distillation trigger."
    )
    assert "async with semaphore" in src, (
        "watch distillation trigger is no longer bounded by `async with semaphore`."
    )
    # The unbounded form is the bug: a bare create_task on record_new_memory with
    # no semaphore wrapper lets a burst of watch batches spawn an unbounded herd.
    assert "create_task(scheduler.record_new_memory" not in src, (
        "watch_service.handle_changes dispatches record_new_memory unbounded; "
        "wrap it in the shared semaphore (BUG 6/7 thundering herd)."
    )