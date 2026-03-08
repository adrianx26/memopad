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

"""Prepared statement tests."""

from stoolap import Database


def test_prepared_execute():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    stmt = db.prepare("INSERT INTO users VALUES ($1, $2)")
    stmt.execute([1, "Alice"])
    stmt.execute([2, "Bob"])

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"
    assert rows[1]["name"] == "Bob"
    db.close()


def test_prepared_query():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    db.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])

    stmt = db.prepare("SELECT * FROM users WHERE id = $1")

    row1 = stmt.query_one([1])
    assert row1["name"] == "Alice"

    row2 = stmt.query_one([2])
    assert row2["name"] == "Bob"
    db.close()


def test_prepared_query_raw():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    stmt = db.prepare("SELECT id, name FROM users WHERE id = $1")
    raw = stmt.query_raw([1])
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"]]
    db.close()


def test_prepared_sql_property():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    stmt = db.prepare("SELECT * FROM users WHERE id = $1")
    assert stmt.sql == "SELECT * FROM users WHERE id = $1"
    db.close()


def test_prepared_execute_batch():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    stmt = db.prepare("INSERT INTO users VALUES ($1, $2)")
    changes = stmt.execute_batch([
        [1, "Alice"],
        [2, "Bob"],
        [3, "Charlie"],
    ])
    assert changes == 3

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 3
    db.close()


def test_prepared_reuse():
    """Prepared statements should be faster on repeated calls (no re-parsing)."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE kv (k INTEGER PRIMARY KEY, val TEXT)")

    insert = db.prepare("INSERT INTO kv VALUES ($1, $2)")
    lookup = db.prepare("SELECT val FROM kv WHERE k = $1")

    for i in range(100):
        insert.execute([i, f"value_{i}"])

    for i in range(100):
        row = lookup.query_one([i])
        assert row is not None
        assert row["val"] == f"value_{i}"

    db.close()
