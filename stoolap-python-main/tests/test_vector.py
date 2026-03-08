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

"""Vector type and similarity search tests."""

import math

from stoolap import Database, Vector


def test_vector_class():
    """Vector wrapper has correct repr and len."""
    v = Vector([1.0, 2.0, 3.0])
    assert len(v) == 3
    assert v.to_list() == [1.0, 2.0, 3.0]
    assert "Vector" in repr(v)


def test_vector_insert_and_query():
    """Insert vectors via Vector() param and read back as list[float]."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding VECTOR(3))")

    db.execute("INSERT INTO embeddings VALUES ($1, $2)", [1, Vector([0.1, 0.2, 0.3])])
    db.execute("INSERT INTO embeddings VALUES ($1, $2)", [2, Vector([0.4, 0.5, 0.6])])

    rows = db.query("SELECT id, embedding FROM embeddings ORDER BY id")
    assert len(rows) == 2

    emb1 = rows[0]["embedding"]
    assert isinstance(emb1, list)
    assert len(emb1) == 3
    assert abs(emb1[0] - 0.1) < 1e-6
    assert abs(emb1[1] - 0.2) < 1e-6
    assert abs(emb1[2] - 0.3) < 1e-6

    emb2 = rows[1]["embedding"]
    assert abs(emb2[0] - 0.4) < 1e-6
    assert abs(emb2[1] - 0.5) < 1e-6
    assert abs(emb2[2] - 0.6) < 1e-6
    db.close()


def test_vector_insert_string_literal():
    """Insert vectors via SQL string literal '[0.1, 0.2, 0.3]'."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.exec("INSERT INTO t VALUES (1, '[0.1, 0.2, 0.3]')")

    row = db.query_one("SELECT v FROM t WHERE id = 1")
    v = row["v"]
    assert isinstance(v, list)
    assert len(v) == 3
    assert abs(v[0] - 0.1) < 1e-6
    db.close()


def test_vector_null():
    """VECTOR columns accept NULL values."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, None])

    row = db.query_one("SELECT v FROM t WHERE id = 1")
    assert row["v"] is None
    db.close()


def test_vector_l2_distance():
    """VEC_DISTANCE_L2 computes Euclidean distance."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 0.0, 0.0])])
    db.execute("INSERT INTO t VALUES ($1, $2)", [2, Vector([0.0, 1.0, 0.0])])

    rows = db.query(
        "SELECT id, VEC_DISTANCE_L2(v, '[1.0, 0.0, 0.0]') AS dist FROM t ORDER BY dist"
    )
    # id=1 should be distance 0 (identical)
    assert rows[0]["id"] == 1
    assert abs(rows[0]["dist"]) < 1e-6
    # id=2 should be distance sqrt(2)
    assert rows[1]["id"] == 2
    assert abs(rows[1]["dist"] - math.sqrt(2)) < 1e-6
    db.close()


def test_vector_cosine_distance():
    """VEC_DISTANCE_COSINE computes cosine distance."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 0.0, 0.0])])
    db.execute("INSERT INTO t VALUES ($1, $2)", [2, Vector([0.0, 1.0, 0.0])])
    db.execute("INSERT INTO t VALUES ($1, $2)", [3, Vector([1.0, 0.0, 0.0])])

    rows = db.query(
        "SELECT id, VEC_DISTANCE_COSINE(v, '[1.0, 0.0, 0.0]') AS dist FROM t ORDER BY dist"
    )
    # id=1 and id=3: identical direction -> cosine distance 0
    assert abs(rows[0]["dist"]) < 1e-6
    assert abs(rows[1]["dist"]) < 1e-6
    # id=2: orthogonal -> cosine distance 1
    assert abs(rows[2]["dist"] - 1.0) < 1e-6
    db.close()


def test_vector_ip_distance():
    """VEC_DISTANCE_IP computes negative inner product."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(2))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 2.0])])
    db.execute("INSERT INTO t VALUES ($1, $2)", [2, Vector([3.0, 4.0])])

    row = db.query_one(
        "SELECT VEC_DISTANCE_IP(v, '[1.0, 1.0]') AS dist FROM t WHERE id = 1"
    )
    # dot(1,2 . 1,1) = 1+2 = 3, negative = -3
    assert abs(row["dist"] - (-3.0)) < 1e-6
    db.close()


def test_vector_knn_search():
    """k-NN search with ORDER BY distance LIMIT."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE items (id INTEGER PRIMARY KEY, v VECTOR(3))")

    vectors = [
        (1, [1.0, 0.0, 0.0]),
        (2, [0.0, 1.0, 0.0]),
        (3, [0.0, 0.0, 1.0]),
        (4, [0.9, 0.1, 0.0]),
        (5, [0.0, 0.9, 0.1]),
    ]
    for id_, v in vectors:
        db.execute("INSERT INTO items VALUES ($1, $2)", [id_, Vector(v)])

    # Find 2 nearest to [1,0,0]
    rows = db.query(
        "SELECT id, VEC_DISTANCE_L2(v, '[1.0, 0.0, 0.0]') AS dist "
        "FROM items ORDER BY dist LIMIT 2"
    )
    assert len(rows) == 2
    assert rows[0]["id"] == 1  # exact match
    assert rows[1]["id"] == 4  # closest neighbor
    db.close()


def test_vector_dims():
    """VEC_DIMS returns dimension count."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(5))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 2.0, 3.0, 4.0, 5.0])])

    row = db.query_one("SELECT VEC_DIMS(v) AS dims FROM t WHERE id = 1")
    assert row["dims"] == 5
    db.close()


def test_vector_norm():
    """VEC_NORM returns L2 norm."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([3.0, 4.0, 0.0])])

    row = db.query_one("SELECT VEC_NORM(v) AS norm FROM t WHERE id = 1")
    assert abs(row["norm"] - 5.0) < 1e-6
    db.close()


def test_vector_to_text():
    """VEC_TO_TEXT converts vector to string representation."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 2.0, 3.0])])

    row = db.query_one("SELECT VEC_TO_TEXT(v) AS txt FROM t WHERE id = 1")
    assert isinstance(row["txt"], str)
    assert "[" in row["txt"]
    assert "1.0" in row["txt"]
    assert "2.0" in row["txt"]
    assert "3.0" in row["txt"]
    db.close()


def test_vector_with_hnsw_index():
    """HNSW index accelerates k-NN search."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE docs (id INTEGER PRIMARY KEY, embedding VECTOR(4))")
    db.exec("CREATE INDEX idx_emb ON docs(embedding) USING HNSW WITH (metric = 'cosine')")

    vectors = [
        (1, [1.0, 0.0, 0.0, 0.0]),
        (2, [0.0, 1.0, 0.0, 0.0]),
        (3, [0.0, 0.0, 1.0, 0.0]),
        (4, [0.0, 0.0, 0.0, 1.0]),
        (5, [0.7, 0.7, 0.0, 0.0]),
    ]
    for id_, v in vectors:
        db.execute("INSERT INTO docs VALUES ($1, $2)", [id_, Vector(v)])

    rows = db.query(
        "SELECT id, VEC_DISTANCE_COSINE(embedding, '[1.0, 0.0, 0.0, 0.0]') AS dist "
        "FROM docs ORDER BY dist LIMIT 2"
    )
    assert len(rows) == 2
    assert rows[0]["id"] == 1  # exact match
    db.close()


def test_vector_batch_insert():
    """execute_batch works with Vector parameters."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(2))")

    params = [[i, Vector([float(i), float(i + 1)])] for i in range(1, 6)]
    count = db.execute_batch("INSERT INTO t VALUES ($1, $2)", params)
    assert count == 5

    rows = db.query("SELECT * FROM t ORDER BY id")
    assert len(rows) == 5
    assert abs(rows[0]["v"][0] - 1.0) < 1e-6
    assert abs(rows[4]["v"][1] - 6.0) < 1e-6
    db.close()


def test_vector_prepared_statement():
    """Prepared statements work with Vector parameters."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")

    stmt = db.prepare("INSERT INTO t VALUES ($1, $2)")
    stmt.execute([1, Vector([0.1, 0.2, 0.3])])
    stmt.execute([2, Vector([0.4, 0.5, 0.6])])

    rows = db.query("SELECT * FROM t ORDER BY id")
    assert len(rows) == 2
    assert abs(rows[0]["v"][0] - 0.1) < 1e-6
    db.close()


def test_vector_transaction():
    """Vectors work within transactions."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")

    tx = db.begin()
    tx.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 2.0, 3.0])])
    tx.execute("INSERT INTO t VALUES ($1, $2)", [2, Vector([4.0, 5.0, 6.0])])
    tx.commit()

    rows = db.query("SELECT * FROM t ORDER BY id")
    assert len(rows) == 2
    assert abs(rows[1]["v"][2] - 6.0) < 1e-6
    db.close()


def test_vector_transaction_rollback():
    """Rolled-back vector inserts are not visible."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")

    tx = db.begin()
    tx.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([1.0, 2.0, 3.0])])
    tx.rollback()

    rows = db.query("SELECT * FROM t")
    assert len(rows) == 0
    db.close()


async def test_async_vector():
    """Async API works with vectors."""
    from stoolap import AsyncDatabase

    db = await AsyncDatabase.open(":memory:")
    await db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v VECTOR(3))")
    await db.execute("INSERT INTO t VALUES ($1, $2)", [1, Vector([0.1, 0.2, 0.3])])

    rows = await db.query("SELECT * FROM t")
    assert len(rows) == 1
    assert isinstance(rows[0]["v"], list)
    assert abs(rows[0]["v"][0] - 0.1) < 1e-6
    await db.close()
