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

"""Basic CRUD operations tests."""

from stoolap import Database, StoolapError
import pytest


def test_open_memory():
    db = Database.open(":memory:")
    assert db is not None
    db.close()


def test_open_empty_string():
    db = Database.open("")
    assert db is not None
    db.close()


def test_create_table():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)")
    rows = db.query("SHOW TABLES")
    table_names = [r["table_name"] for r in rows]
    assert "users" in table_names
    db.close()


def test_insert_and_query():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")

    db.execute("INSERT INTO users VALUES ($1, $2, $3)", [1, "Alice", 30])
    db.execute("INSERT INTO users VALUES ($1, $2, $3)", [2, "Bob", 25])

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    assert rows[0] == {"id": 1, "name": "Alice", "age": 30}
    assert rows[1] == {"id": 2, "name": "Bob", "age": 25}
    db.close()


def test_named_params():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    db.execute(
        "INSERT INTO users VALUES (:id, :name)",
        {"id": 1, "name": "Alice"},
    )

    row = db.query_one("SELECT * FROM users WHERE id = :id", {"id": 1})
    assert row is not None
    assert row["name"] == "Alice"
    db.close()


def test_query_one_returns_none():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    row = db.query_one("SELECT * FROM users WHERE id = $1", [999])
    assert row is None
    db.close()


def test_query_raw():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    db.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])

    raw = db.query_raw("SELECT id, name FROM users ORDER BY id")
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"], [2, "Bob"]]
    db.close()


def test_update():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    changes = db.execute("UPDATE users SET name = $1 WHERE id = $2", ["Alicia", 1])
    assert changes == 1

    row = db.query_one("SELECT name FROM users WHERE id = $1", [1])
    assert row["name"] == "Alicia"
    db.close()


def test_delete():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    db.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])

    changes = db.execute("DELETE FROM users WHERE id = $1", [1])
    assert changes == 1

    rows = db.query("SELECT * FROM users")
    assert len(rows) == 1
    assert rows[0]["id"] == 2
    db.close()


def test_exec_multiple_statements():
    db = Database.open(":memory:")
    db.exec("""
        CREATE TABLE a (id INTEGER PRIMARY KEY);
        CREATE TABLE b (id INTEGER PRIMARY KEY);
    """)

    rows = db.query("SHOW TABLES")
    table_names = [r["table_name"] for r in rows]
    assert "a" in table_names
    assert "b" in table_names
    db.close()


def test_type_mapping():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE types (i INTEGER, f FLOAT, t TEXT, b BOOLEAN)")
    db.execute("INSERT INTO types VALUES ($1, $2, $3, $4)", [42, 3.14, "hello", True])

    row = db.query_one("SELECT * FROM types")
    assert row["i"] == 42
    assert abs(row["f"] - 3.14) < 0.001
    assert row["t"] == "hello"
    assert row["b"] is True
    db.close()


def test_null_values():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE nullable (id INTEGER PRIMARY KEY, val TEXT)")
    db.execute("INSERT INTO nullable VALUES ($1, $2)", [1, None])

    row = db.query_one("SELECT * FROM nullable WHERE id = $1", [1])
    assert row["val"] is None
    db.close()


def test_tuple_params():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", (1, "Alice"))

    row = db.query_one("SELECT * FROM users WHERE id = $1", (1,))
    assert row["name"] == "Alice"
    db.close()


def test_error_handling():
    db = Database.open(":memory:")
    with pytest.raises(StoolapError):
        db.execute("SELECTX * FROM nonexistent")
    db.close()


def test_execute_batch():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    changes = db.execute_batch(
        "INSERT INTO users VALUES ($1, $2)",
        [[1, "Alice"], [2, "Bob"], [3, "Charlie"]],
    )
    assert changes == 3

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 3
    assert rows[0]["name"] == "Alice"
    assert rows[2]["name"] == "Charlie"
    db.close()
