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

"""Transaction tests."""

from stoolap import Database, StoolapError
import pytest


def test_commit():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = db.begin()
    tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    tx.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])
    tx.commit()

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    db.close()


def test_rollback():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = db.begin()
    tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    tx.rollback()

    rows = db.query("SELECT * FROM users")
    assert len(rows) == 0
    db.close()


def test_context_manager_commit():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    with db.begin() as tx:
        tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
        tx.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    db.close()


def test_context_manager_rollback_on_exception():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    with pytest.raises(ValueError):
        with db.begin() as tx:
            tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
            raise ValueError("test error")

    rows = db.query("SELECT * FROM users")
    assert len(rows) == 0
    db.close()


def test_tx_query():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = db.begin()
    tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])

    # Read within transaction sees uncommitted data
    rows = tx.query("SELECT * FROM users")
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"

    one = tx.query_one("SELECT name FROM users WHERE id = $1", [1])
    assert one["name"] == "Alice"

    raw = tx.query_raw("SELECT id, name FROM users")
    assert raw["columns"] == ["id", "name"]
    assert raw["rows"] == [[1, "Alice"]]

    tx.commit()
    db.close()


def test_tx_execute_batch():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = db.begin()
    changes = tx.execute_batch(
        "INSERT INTO users VALUES ($1, $2)",
        [[1, "Alice"], [2, "Bob"]],
    )
    assert changes == 2
    tx.commit()

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    db.close()


def test_tx_positional_params():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

    tx = db.begin()
    tx.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    tx.execute("INSERT INTO users VALUES ($1, $2)", (2, "Bob"))
    tx.commit()

    rows = db.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"
    assert rows[1]["name"] == "Bob"
    db.close()
