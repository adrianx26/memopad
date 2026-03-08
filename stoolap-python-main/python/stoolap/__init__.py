# Copyright 2025 Stoolap Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Stoolap - High-performance embedded SQL database for Python.

Usage:
    from stoolap import Database

    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    rows = db.query("SELECT * FROM users")
    # [{"id": 1, "name": "Alice"}]

Async usage:
    from stoolap import AsyncDatabase

    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    rows = await db.query("SELECT * FROM users")
    await db.close()
"""

from stoolap._stoolap import (
    Database,
    Transaction,
    PreparedStatement,
    Vector,
    StoolapError,
)

import asyncio


class AsyncDatabase:
    """Async wrapper around Database.

    All methods release the GIL and run on a thread executor,
    so they won't block the asyncio event loop.
    """

    __slots__ = ("_db",)

    def __init__(self, db: Database):
        self._db = db

    @classmethod
    async def open(cls, path: str = ":memory:") -> "AsyncDatabase":
        db = await asyncio.to_thread(Database.open, path)
        return cls(db)

    async def execute(self, sql: str, params=None) -> int:
        return await asyncio.to_thread(self._db.execute, sql, params)

    async def exec(self, sql: str) -> None:
        return await asyncio.to_thread(self._db.exec, sql)

    async def query(self, sql: str, params=None) -> list:
        return await asyncio.to_thread(self._db.query, sql, params)

    async def query_one(self, sql: str, params=None):
        return await asyncio.to_thread(self._db.query_one, sql, params)

    async def query_raw(self, sql: str, params=None) -> dict:
        return await asyncio.to_thread(self._db.query_raw, sql, params)

    async def execute_batch(self, sql: str, params_list: list) -> int:
        return await asyncio.to_thread(self._db.execute_batch, sql, params_list)

    def prepare(self, sql: str) -> "AsyncPreparedStatement":
        stmt = self._db.prepare(sql)
        return AsyncPreparedStatement(stmt)

    async def begin(self) -> "AsyncTransaction":
        tx = await asyncio.to_thread(self._db.begin)
        return AsyncTransaction(tx)

    async def close(self) -> None:
        await asyncio.to_thread(self._db.close)

    def __repr__(self) -> str:
        return "AsyncDatabase(open)"


class AsyncTransaction:
    """Async wrapper around Transaction.

    Can be used as an async context manager:
        async with await db.begin() as tx:
            await tx.execute(...)
    """

    __slots__ = ("_tx",)

    def __init__(self, tx: Transaction):
        self._tx = tx

    async def execute(self, sql: str, params=None) -> int:
        return await asyncio.to_thread(self._tx.execute, sql, params)

    async def query(self, sql: str, params=None) -> list:
        return await asyncio.to_thread(self._tx.query, sql, params)

    async def query_one(self, sql: str, params=None):
        return await asyncio.to_thread(self._tx.query_one, sql, params)

    async def query_raw(self, sql: str, params=None) -> dict:
        return await asyncio.to_thread(self._tx.query_raw, sql, params)

    async def execute_batch(self, sql: str, params_list: list) -> int:
        return await asyncio.to_thread(self._tx.execute_batch, sql, params_list)

    async def commit(self) -> None:
        await asyncio.to_thread(self._tx.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._tx.rollback)

    async def __aenter__(self) -> "AsyncTransaction":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        return False

    def __repr__(self) -> str:
        return repr(self._tx)


class AsyncPreparedStatement:
    """Async wrapper around PreparedStatement."""

    __slots__ = ("_stmt",)

    def __init__(self, stmt: PreparedStatement):
        self._stmt = stmt

    async def execute(self, params=None) -> int:
        return await asyncio.to_thread(self._stmt.execute, params)

    async def query(self, params=None) -> list:
        return await asyncio.to_thread(self._stmt.query, params)

    async def query_one(self, params=None):
        return await asyncio.to_thread(self._stmt.query_one, params)

    async def query_raw(self, params=None) -> dict:
        return await asyncio.to_thread(self._stmt.query_raw, params)

    async def execute_batch(self, params_list: list) -> int:
        return await asyncio.to_thread(self._stmt.execute_batch, params_list)

    @property
    def sql(self) -> str:
        return self._stmt.sql

    def __repr__(self) -> str:
        return repr(self._stmt)


__all__ = [
    "Database",
    "Transaction",
    "PreparedStatement",
    "Vector",
    "AsyncDatabase",
    "AsyncTransaction",
    "AsyncPreparedStatement",
    "StoolapError",
]
