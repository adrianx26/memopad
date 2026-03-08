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

"""Named parameter tests across all APIs."""

import pytest
from stoolap import Database, AsyncDatabase


@pytest.fixture
def db():
    d = Database.open(":memory:")
    d.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    d.execute(
        "INSERT INTO users VALUES (:id, :name, :email)",
        {"id": 1, "name": "Alice", "email": "alice@example.com"},
    )
    d.execute(
        "INSERT INTO users VALUES (:id, :name, :email)",
        {"id": 2, "name": "Bob", "email": "bob@example.com"},
    )
    yield d
    d.close()


# --- Database-level named params ---


def test_db_execute_insert(db):
    changes = db.execute(
        "INSERT INTO users VALUES (:id, :name, :email)",
        {"id": 3, "name": "Charlie", "email": "c@example.com"},
    )
    assert changes == 1
    row = db.query_one("SELECT name FROM users WHERE id = :id", {"id": 3})
    assert row["name"] == "Charlie"


def test_db_execute_update(db):
    changes = db.execute(
        "UPDATE users SET email = :email WHERE id = :id",
        {"email": "new@example.com", "id": 1},
    )
    assert changes == 1
    row = db.query_one("SELECT email FROM users WHERE id = :id", {"id": 1})
    assert row["email"] == "new@example.com"


def test_db_execute_delete(db):
    changes = db.execute("DELETE FROM users WHERE id = :id", {"id": 2})
    assert changes == 1
    rows = db.query("SELECT * FROM users")
    assert len(rows) == 1


def test_db_query(db):
    rows = db.query("SELECT * FROM users WHERE name = :name", {"name": "Alice"})
    assert len(rows) == 1
    assert rows[0]["email"] == "alice@example.com"


def test_db_query_one(db):
    row = db.query_one("SELECT * FROM users WHERE id = :id", {"id": 2})
    assert row is not None
    assert row["name"] == "Bob"


def test_db_query_one_none(db):
    row = db.query_one("SELECT * FROM users WHERE id = :id", {"id": 999})
    assert row is None


def test_db_query_raw(db):
    raw = db.query_raw("SELECT id, name FROM users WHERE id = :id", {"id": 1})
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"]]


# --- Transaction-level named params ---


def test_tx_execute_insert(db):
    with db.begin() as tx:
        tx.execute(
            "INSERT INTO users VALUES (:id, :name, :email)",
            {"id": 3, "name": "Charlie", "email": "c@example.com"},
        )
    row = db.query_one("SELECT name FROM users WHERE id = :id", {"id": 3})
    assert row["name"] == "Charlie"


def test_tx_execute_update(db):
    with db.begin() as tx:
        changes = tx.execute(
            "UPDATE users SET email = :email WHERE id = :id",
            {"email": "updated@example.com", "id": 1},
        )
        assert changes == 1
    row = db.query_one("SELECT email FROM users WHERE id = :id", {"id": 1})
    assert row["email"] == "updated@example.com"


def test_tx_execute_delete(db):
    with db.begin() as tx:
        changes = tx.execute("DELETE FROM users WHERE id = :id", {"id": 2})
        assert changes == 1
    rows = db.query("SELECT * FROM users")
    assert len(rows) == 1


def test_tx_query(db):
    with db.begin() as tx:
        tx.execute(
            "INSERT INTO users VALUES (:id, :name, :email)",
            {"id": 3, "name": "Charlie", "email": "c@example.com"},
        )
        rows = tx.query("SELECT * FROM users WHERE name = :name", {"name": "Charlie"})
        assert len(rows) == 1
        assert rows[0]["email"] == "c@example.com"


def test_tx_query_one(db):
    with db.begin() as tx:
        row = tx.query_one("SELECT * FROM users WHERE id = :id", {"id": 1})
        assert row is not None
        assert row["name"] == "Alice"


def test_tx_query_raw(db):
    with db.begin() as tx:
        raw = tx.query_raw("SELECT id, name FROM users WHERE id = :id", {"id": 2})
        assert raw["columns"] == ["id", "name"]
        assert raw["rows"] == [[2, "Bob"]]


def test_tx_rollback_named_params(db):
    """Named-param INSERT should be rolled back on exception."""
    with pytest.raises(ValueError):
        with db.begin() as tx:
            tx.execute(
                "INSERT INTO users VALUES (:id, :name, :email)",
                {"id": 3, "name": "Charlie", "email": "c@example.com"},
            )
            raise ValueError("force rollback")
    rows = db.query("SELECT * FROM users")
    assert len(rows) == 2  # unchanged


# --- Prepared statement named params ---


def test_prepared_execute_named(db):
    stmt = db.prepare("INSERT INTO users VALUES (:id, :name, :email)")
    stmt.execute({"id": 3, "name": "Charlie", "email": "c@example.com"})
    row = db.query_one("SELECT name FROM users WHERE id = :id", {"id": 3})
    assert row["name"] == "Charlie"


def test_prepared_query_named(db):
    stmt = db.prepare("SELECT * FROM users WHERE id = :id")
    rows = stmt.query({"id": 1})
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"


def test_prepared_query_one_named(db):
    stmt = db.prepare("SELECT * FROM users WHERE id = :id")
    row = stmt.query_one({"id": 2})
    assert row["name"] == "Bob"

    none_row = stmt.query_one({"id": 999})
    assert none_row is None


def test_prepared_query_raw_named(db):
    stmt = db.prepare("SELECT id, name FROM users WHERE id = :id")
    raw = stmt.query_raw({"id": 1})
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"]]


# --- Async named params ---


@pytest.mark.asyncio
async def test_async_execute_named():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute(
        "INSERT INTO users VALUES (:id, :name)", {"id": 1, "name": "Alice"}
    )

    rows = await db.query("SELECT * FROM users WHERE id = :id", {"id": 1})
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    await db.close()


@pytest.mark.asyncio
async def test_async_query_one_named():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute(
        "INSERT INTO users VALUES (:id, :name)", {"id": 1, "name": "Alice"}
    )

    row = await db.query_one("SELECT * FROM users WHERE id = :id", {"id": 1})
    assert row is not None
    assert row["name"] == "Alice"
    await db.close()


@pytest.mark.asyncio
async def test_async_tx_named():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    async with await db.begin() as tx:
        await tx.execute(
            "INSERT INTO users VALUES (:id, :name)", {"id": 1, "name": "Alice"}
        )

    rows = await db.query("SELECT * FROM users")
    assert len(rows) == 1
    await db.close()


# --- Edge cases ---


def test_named_params_with_prefix_colon():
    """Keys with leading ':' should still work."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    db.execute("INSERT INTO t VALUES (:id, :val)", {":id": 1, ":val": "hello"})
    row = db.query_one("SELECT val FROM t WHERE id = :id", {":id": 1})
    assert row["val"] == "hello"
    db.close()
