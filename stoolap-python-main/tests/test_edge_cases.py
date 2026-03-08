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

"""Edge cases and error handling tests."""

import pytest
from stoolap import Database, AsyncDatabase, StoolapError


# --- Error handling ---


def test_invalid_sql():
    db = Database.open(":memory:")
    with pytest.raises(StoolapError):
        db.execute("SELECTX * FROM foo")
    db.close()


def test_table_not_found():
    db = Database.open(":memory:")
    with pytest.raises(StoolapError):
        db.query("SELECT * FROM nonexistent")
    db.close()


def test_duplicate_primary_key():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO t VALUES ($1)", [1])
    with pytest.raises(StoolapError):
        db.execute("INSERT INTO t VALUES ($1)", [1])
    db.close()


def test_constraint_not_null():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    with pytest.raises(StoolapError):
        db.execute("INSERT INTO t VALUES ($1, $2)", [1, None])
    db.close()


# --- Empty results ---


def test_query_empty_table():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    rows = db.query("SELECT * FROM t")
    assert rows == []
    db.close()


def test_query_raw_empty():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    raw = db.query_raw("SELECT * FROM t")
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == []
    db.close()


def test_query_one_empty():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    row = db.query_one("SELECT * FROM t WHERE id = $1", [999])
    assert row is None
    db.close()


# --- No params ---


def test_execute_no_params():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY DEFAULT 1, val TEXT DEFAULT 'hi')")
    db.execute("INSERT INTO t (id) VALUES (1)")
    row = db.query_one("SELECT * FROM t")
    assert row["val"] == "hi"
    db.close()


def test_query_no_params():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO t VALUES ($1)", [1])
    rows = db.query("SELECT * FROM t")
    assert len(rows) == 1
    db.close()


# --- Transaction edge cases ---


def test_tx_use_after_commit():
    """Using a committed transaction should raise an error."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    tx = db.begin()
    tx.execute("INSERT INTO t VALUES ($1)", [1])
    tx.commit()

    with pytest.raises(StoolapError):
        tx.execute("INSERT INTO t VALUES ($1)", [2])
    db.close()


def test_tx_use_after_rollback():
    """Using a rolled-back transaction should raise an error."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    tx = db.begin()
    tx.execute("INSERT INTO t VALUES ($1)", [1])
    tx.rollback()

    with pytest.raises(StoolapError):
        tx.execute("INSERT INTO t VALUES ($1)", [2])
    db.close()


def test_tx_double_commit():
    """Committing twice should raise an error."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    tx = db.begin()
    tx.execute("INSERT INTO t VALUES ($1)", [1])
    tx.commit()

    with pytest.raises(StoolapError):
        tx.commit()
    db.close()


def test_tx_repr():
    db = Database.open(":memory:")
    tx = db.begin()
    assert "active" in repr(tx)
    tx.commit()
    assert "closed" in repr(tx)
    db.close()


# --- Batch edge cases ---


def test_execute_batch_empty():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    changes = db.execute_batch("INSERT INTO t VALUES ($1)", [])
    assert changes == 0
    db.close()


def test_execute_batch_single():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    changes = db.execute_batch("INSERT INTO t VALUES ($1)", [[1]])
    assert changes == 1
    db.close()


def test_execute_batch_many():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    changes = db.execute_batch(
        "INSERT INTO t VALUES ($1, $2)",
        [[i, f"val_{i}"] for i in range(1000)],
    )
    assert changes == 1000
    rows = db.query("SELECT COUNT(*) as cnt FROM t")
    assert rows[0]["cnt"] == 1000
    db.close()


# --- Prepared statement edge cases ---


def test_prepared_sql_property():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    stmt = db.prepare("SELECT * FROM t WHERE id = $1")
    assert stmt.sql == "SELECT * FROM t WHERE id = $1"
    db.close()


def test_prepared_multiple_queries():
    """Same prepared statement used for different params."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_batch(
        "INSERT INTO t VALUES ($1, $2)",
        [[1, "Alice"], [2, "Bob"], [3, "Charlie"]],
    )

    stmt = db.prepare("SELECT name FROM t WHERE id = $1")
    assert stmt.query_one([1])["name"] == "Alice"
    assert stmt.query_one([2])["name"] == "Bob"
    assert stmt.query_one([3])["name"] == "Charlie"
    assert stmt.query_one([999]) is None
    db.close()


# --- SHOW TABLES ---


def test_show_tables():
    db = Database.open(":memory:")
    db.exec("""
        CREATE TABLE alpha (id INTEGER PRIMARY KEY);
        CREATE TABLE beta (id INTEGER PRIMARY KEY);
        CREATE TABLE gamma (id INTEGER PRIMARY KEY);
    """)

    rows = db.query("SHOW TABLES")
    names = sorted([r["table_name"] for r in rows])
    assert names == ["alpha", "beta", "gamma"]
    db.close()


# --- Large data ---


def test_large_text():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    large = "x" * 100_000
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, large])
    row = db.query_one("SELECT val FROM t WHERE id = $1", [1])
    assert len(row["val"]) == 100_000
    assert row["val"] == large
    db.close()


def test_many_columns():
    db = Database.open(":memory:")
    cols = ", ".join(f"c{i} INTEGER" for i in range(50))
    db.exec(f"CREATE TABLE wide (id INTEGER PRIMARY KEY, {cols})")

    placeholders = ", ".join(f"${i+1}" for i in range(51))
    params = list(range(51))  # id=0, c0=1, c1=2, ...
    db.execute(f"INSERT INTO wide VALUES ({placeholders})", params)

    row = db.query_one("SELECT * FROM wide WHERE id = $1", [0])
    assert row["c0"] == 1
    assert row["c49"] == 50
    db.close()


# --- Async edge cases ---


@pytest.mark.asyncio
async def test_async_error_handling():
    db = await AsyncDatabase.open(":memory:")
    with pytest.raises(StoolapError):
        await db.query("SELECT * FROM nonexistent")
    await db.close()


@pytest.mark.asyncio
async def test_async_tx_rollback_on_exception():
    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    try:
        async with await db.begin() as tx:
            await tx.execute("INSERT INTO t VALUES ($1)", [1])
            raise ValueError("force rollback")
    except ValueError:
        pass

    rows = await db.query("SELECT * FROM t")
    assert len(rows) == 0
    await db.close()
