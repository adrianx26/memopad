"""Exercise SQLite's in-process write queue under async contention."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db


@pytest.mark.asyncio
async def test_concurrent_sqlite_writes_wait_and_complete(tmp_path: Path):
    database_path = tmp_path / "concurrent-writes.db"
    engine = db._create_sqlite_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        db.DatabaseType.FILESYSTEM,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    active_writes = 0
    max_active_writes = 0
    active_writes_lock = asyncio.Lock()

    try:
        async with engine.begin() as connection:
            timeout = await connection.exec_driver_sql("PRAGMA busy_timeout")
            assert timeout.scalar_one() == 30000
            await connection.exec_driver_sql(
                "CREATE TABLE write_probe (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
            )

        async def write(value: int) -> None:
            nonlocal active_writes, max_active_writes
            async with db.scoped_session(session_maker, write=True) as session:
                async with active_writes_lock:
                    active_writes += 1
                    max_active_writes = max(max_active_writes, active_writes)
                try:
                    await session.execute(
                        text("INSERT INTO write_probe (value) VALUES (:value)"),
                        {"value": value},
                    )
                    await asyncio.sleep(0.002)
                finally:
                    async with active_writes_lock:
                        active_writes -= 1

        await asyncio.gather(*(write(value) for value in range(32)))
        assert max_active_writes == 1

        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT COUNT(*) FROM write_probe"))
            assert result.scalar_one() == 32
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_write_queue_releases_after_session_failure(tmp_path: Path):
    database_path = tmp_path / "failed-write.db"
    engine = db._create_sqlite_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        db.DatabaseType.FILESYSTEM,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE write_probe (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
            )

        async def failed_write() -> None:
            async with db.scoped_session(session_maker, write=True) as session:
                await session.execute(text("SELECT 1"))
                raise RuntimeError("rollback the transaction")

        with pytest.raises(RuntimeError, match="rollback the transaction"):
            await failed_write()

        async with db.scoped_session(session_maker, write=True) as session:
            await session.execute(text("INSERT INTO write_probe (value) VALUES (2)"))

        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT value FROM write_probe"))
            assert result.scalars().all() == [2]
    finally:
        await engine.dispose()
