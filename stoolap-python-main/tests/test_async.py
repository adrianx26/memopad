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

"""Async API tests."""

import pytest
from stoolap import AsyncDatabase


@pytest.mark.asyncio
async def test_async_open_and_query():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    rows = await db.query("SELECT * FROM users")
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    await db.close()


@pytest.mark.asyncio
async def test_async_query_one():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    row = await db.query_one("SELECT * FROM users WHERE id = $1", [1])
    assert row is not None
    assert row["name"] == "Alice"

    none_row = await db.query_one("SELECT * FROM users WHERE id = $1", [999])
    assert none_row is None
    await db.close()


@pytest.mark.asyncio
async def test_async_query_raw():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    raw = await db.query_raw("SELECT id, name FROM users")
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"]]
    await db.close()


@pytest.mark.asyncio
async def test_async_transaction():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = await db.begin()
    await tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    await tx.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])
    await tx.commit()

    rows = await db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    await db.close()


@pytest.mark.asyncio
async def test_async_transaction_context_manager():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    async with await db.begin() as tx:
        await tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    rows = await db.query("SELECT * FROM users")
    assert len(rows) == 1
    await db.close()


@pytest.mark.asyncio
async def test_async_transaction_rollback():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    try:
        async with await db.begin() as tx:
            await tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
            raise ValueError("test")
    except ValueError:
        pass

    rows = await db.query("SELECT * FROM users")
    assert len(rows) == 0
    await db.close()


@pytest.mark.asyncio
async def test_async_prepared_statement():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    stmt = db.prepare("INSERT INTO users VALUES ($1, $2)")
    await stmt.execute([1, "Alice"])
    await stmt.execute([2, "Bob"])

    lookup = db.prepare("SELECT * FROM users WHERE id = $1")
    row = await lookup.query_one([1])
    assert row["name"] == "Alice"
    await db.close()


@pytest.mark.asyncio
async def test_async_execute_batch():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    changes = await db.execute_batch(
        "INSERT INTO users VALUES ($1, $2)",
        [[1, "Alice"], [2, "Bob"], [3, "Charlie"]],
    )
    assert changes == 3

    rows = await db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 3
    await db.close()
