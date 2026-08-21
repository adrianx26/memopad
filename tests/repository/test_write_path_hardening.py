"""Pure-logic tests for the write-path hardening added to fix the `assimilate`
intermittent failures (UNIQUE constraint / database is locked / QueuePool).

These tests exercise the new mechanisms directly, without the DB/fixture stack, so
they are stable and fast:

- ``_retry_on_db_locked`` (repository.repository): retries transient SQLite
  "database is locked" with backoff, re-raises everything else.
- ``get_background_task_semaphore`` (deps.services): process-wide singleton so all
  background tasks share one concurrency budget across requests.
"""

import asyncio

import pytest
from sqlalchemy.exc import OperationalError as SAOperationalError

from memopad.deps import services as deps_services
from memopad.deps.services import get_background_task_semaphore
from memopad.repository.repository import _retry_on_db_locked


def _locked_error() -> SAOperationalError:
    """A SQLAlchemy OperationalError whose message contains 'database is locked'."""
    return SAOperationalError("INSERT INTO entity ...", {}, Exception("database is locked"))


def _other_error() -> SAOperationalError:
    """An OperationalError that is NOT a lock error and must not be retried."""
    return SAOperationalError("SELECT ...", {}, Exception("disk I/O error"))


@pytest.mark.asyncio
async def test_retry_on_db_locked_retries_then_succeeds(monkeypatch):
    """A write that fails twice with 'database is locked' then succeeds is retried."""
    # Don't actually sleep between retries.
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(deps_services.asyncio, "sleep", _fake_sleep)
    # repository.asyncio.sleep is the one the decorator uses.
    from memopad.repository import repository as repo_mod

    monkeypatch.setattr(repo_mod.asyncio, "sleep", _fake_sleep)

    calls = {"n": 0}

    @_retry_on_db_locked
    async def _write():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _locked_error()
        return "ok"

    result = await _write()
    assert result == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # backoff between the two retries


@pytest.mark.asyncio
async def test_retry_on_db_locked_does_not_retry_non_lock_errors(monkeypatch):
    """A non-lock OperationalError (e.g. 'disk I/O error') is re-raised immediately."""
    from memopad.repository import repository as repo_mod

    monkeypatch.setattr(repo_mod.asyncio, "sleep", lambda d: pytest.fail("should not sleep"))

    @_retry_on_db_locked
    async def _write():
        raise _other_error()

    with pytest.raises(SAOperationalError):
        await _write()


@pytest.mark.asyncio
async def test_retry_on_db_locked_gives_up_after_max_retries(monkeypatch):
    """After exhausting retries on persistent 'database is locked', the error is raised."""
    from memopad.repository import repository as repo_mod

    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(repo_mod.asyncio, "sleep", _no_sleep)

    @_retry_on_db_locked
    async def _write():
        raise _locked_error()

    with pytest.raises(SAOperationalError):
        await _write()


def test_background_task_semaphore_is_process_wide_singleton():
    """All callers get the same semaphore regardless of the limit they pass."""
    # Reset the module singleton so this test is hermetic and doesn't leak.
    prev = deps_services._background_task_semaphore
    deps_services._background_task_semaphore = None
    try:
        sem_a = get_background_task_semaphore(8)
        sem_b = get_background_task_semaphore(4)  # different limit, same singleton
        assert sem_a is sem_b
        assert isinstance(sem_a, asyncio.Semaphore)
    finally:
        deps_services._background_task_semaphore = prev