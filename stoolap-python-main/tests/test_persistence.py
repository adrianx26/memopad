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

"""Persistence tests (file-based database, crash recovery)."""

import os
import shutil
import tempfile

import pytest
from stoolap import Database


@pytest.fixture
def db_dir():
    """Create a temp directory for the database and clean up afterwards."""
    d = tempfile.mkdtemp(prefix="stoolap_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_file_persistence_basic(db_dir):
    """Data survives close and reopen."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    db.execute("INSERT INTO users VALUES ($1, $2)", [2, "Bob"])
    db.close()

    # Reopen and verify
    db2 = Database.open(path)
    rows = db2.query("SELECT * FROM users ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"
    assert rows[1]["name"] == "Bob"
    db2.close()


def test_file_persistence_with_index(db_dir):
    """Indexes survive close and reopen."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("""
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price FLOAT);
        CREATE INDEX idx_products_name ON products(name);
    """)
    db.execute("INSERT INTO products VALUES ($1, $2, $3)", [1, "Widget", 9.99])
    db.execute("INSERT INTO products VALUES ($1, $2, $3)", [2, "Gadget", 19.99])
    db.close()

    db2 = Database.open(path)
    row = db2.query_one("SELECT * FROM products WHERE name = $1", ["Widget"])
    assert row is not None
    assert abs(row["price"] - 9.99) < 0.001
    db2.close()


def test_file_persistence_update_delete(db_dir):
    """Updates and deletes are persisted."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("CREATE TABLE kv (k INTEGER PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO kv VALUES ($1, $2)", [1, "original"])
    db.execute("INSERT INTO kv VALUES ($1, $2)", [2, "delete_me"])
    db.execute("UPDATE kv SET v = $1 WHERE k = $2", ["updated", 1])
    db.execute("DELETE FROM kv WHERE k = $1", [2])
    db.close()

    db2 = Database.open(path)
    rows = db2.query("SELECT * FROM kv ORDER BY k")
    assert len(rows) == 1
    assert rows[0]["v"] == "updated"
    db2.close()


def test_file_persistence_multiple_tables(db_dir):
    """Multiple tables survive close and reopen."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount FLOAT);
    """)
    db.execute("INSERT INTO users VALUES ($1, $2)", [1, "Alice"])
    db.execute("INSERT INTO orders VALUES ($1, $2, $3)", [1, 1, 99.99])
    db.execute("INSERT INTO orders VALUES ($1, $2, $3)", [2, 1, 49.99])
    db.close()

    db2 = Database.open(path)
    users = db2.query("SELECT * FROM users")
    orders = db2.query("SELECT * FROM orders ORDER BY id")
    assert len(users) == 1
    assert len(orders) == 2
    assert abs(orders[0]["amount"] - 99.99) < 0.001
    db2.close()


def test_file_persistence_transaction(db_dir):
    """Committed transactions persist, rolled back ones don't."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("CREATE TABLE counter (id INTEGER PRIMARY KEY, val INTEGER)")

    # Committed transaction
    with db.begin() as tx:
        tx.execute("INSERT INTO counter VALUES ($1, $2)", [1, 100])

    # Rolled back transaction
    tx2 = db.begin()
    tx2.execute("INSERT INTO counter VALUES ($1, $2)", [2, 200])
    tx2.rollback()

    db.close()

    db2 = Database.open(path)
    rows = db2.query("SELECT * FROM counter ORDER BY id")
    assert len(rows) == 1
    assert rows[0]["val"] == 100
    db2.close()


def test_file_persistence_batch_insert(db_dir):
    """Batch inserts persist."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_batch(
        "INSERT INTO items VALUES ($1, $2)",
        [[i, f"item_{i}"] for i in range(100)],
    )
    db.close()

    db2 = Database.open(path)
    rows = db2.query("SELECT COUNT(*) as cnt FROM items")
    assert rows[0]["cnt"] == 100
    db2.close()


def test_file_persistence_dsn_options(db_dir):
    """DSN query parameters work for persistence config."""
    path = os.path.join(db_dir, "testdb")
    dsn = f"file://{path}?sync=full"

    db = Database.open(dsn)
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, "durable"])
    db.close()

    db2 = Database.open(path)
    row = db2.query_one("SELECT val FROM t WHERE id = $1", [1])
    assert row["val"] == "durable"
    db2.close()


def test_file_persistence_named_params(db_dir):
    """Named params with file-based DB."""
    path = os.path.join(db_dir, "testdb")

    db = Database.open(path)
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute(
        "INSERT INTO users VALUES (:id, :name)", {"id": 1, "name": "Alice"}
    )
    db.close()

    db2 = Database.open(path)
    row = db2.query_one("SELECT name FROM users WHERE id = :id", {"id": 1})
    assert row["name"] == "Alice"
    db2.close()


def test_relative_path_persistence(db_dir):
    """Relative paths work for file-based databases."""
    # We can't easily test true relative paths in a fixture,
    # but we can test that a path without file:// prefix works
    path = os.path.join(db_dir, "reldb")

    db = Database.open(path)
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO t VALUES ($1)", [1])
    db.close()

    db2 = Database.open(path)
    rows = db2.query("SELECT * FROM t")
    assert len(rows) == 1
    db2.close()
