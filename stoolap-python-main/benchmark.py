#!/usr/bin/env python3
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

"""Stoolap vs SQLite (stdlib) Python benchmark.

Both drivers use synchronous methods for fair comparison.
Matches the Node.js benchmark.mjs test set and ordering.

Run:  python benchmark.py
"""

import sqlite3
import time

from stoolap import Database as StoolapDB

ROW_COUNT = 10_000
ITERATIONS = 500        # Point queries
ITERATIONS_MEDIUM = 250  # Index scans, aggregations
ITERATIONS_HEAVY = 50    # Full scans, JOINs
WARMUP = 10

# ============================================================
# Helpers
# ============================================================

def fmt_us(us: float) -> str:
    return f"{us:.3f}".rjust(15)


def fmt_ratio(stoolap_us: float, sqlite_us: float) -> str:
    if stoolap_us <= 0 or sqlite_us <= 0:
        return "      -"
    ratio = sqlite_us / stoolap_us
    if ratio >= 1:
        return f"{ratio:.2f}x".rjust(10)
    else:
        return f"{1 / ratio:.2f}x".rjust(9) + "*"


stoolap_wins = 0
sqlite_wins = 0


def print_row(name: str, stoolap_us: float, sqlite_us: float) -> None:
    global stoolap_wins, sqlite_wins
    ratio = fmt_ratio(stoolap_us, sqlite_us)
    if stoolap_us < sqlite_us:
        stoolap_wins += 1
    elif sqlite_us < stoolap_us:
        sqlite_wins += 1
    print(f"{name:<28} | {fmt_us(stoolap_us)} | {fmt_us(sqlite_us)} | {ratio}")


def print_header(section: str) -> None:
    print()
    print("=" * 80)
    print(section)
    print("=" * 80)
    print(f"{'Operation':<28} | {'Stoolap (μs)':>15} | {'SQLite (μs)':>15} | {'Ratio':>10}")
    print("-" * 80)


def seed_random(i: int) -> int:
    return (i * 1103515245 + 12345) & 0x7FFFFFFF


def bench_us(fn, iters: int) -> float:
    """Run fn() iters times, return average microseconds per call."""
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0
    return (elapsed * 1_000_000) / iters


# ============================================================
# Main
# ============================================================

def main() -> None:
    print("Stoolap vs SQLite (stdlib) — Python Benchmark")
    print(f"Configuration: {ROW_COUNT} rows, {ITERATIONS} iterations per test")
    print("All operations are synchronous — fair comparison")
    print("Ratio > 1x = Stoolap faster  |  * = SQLite faster\n")

    # --- Stoolap setup ---
    sdb = StoolapDB.open(":memory:")
    sdb.exec("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            age INTEGER NOT NULL,
            balance FLOAT NOT NULL,
            active BOOLEAN NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    sdb.exec("CREATE INDEX idx_users_age ON users(age)")
    sdb.exec("CREATE INDEX idx_users_active ON users(active)")

    # --- SQLite setup (autocommit for fair write comparison) ---
    # isolation_level=None gives true autocommit (works on Python 3.9+)
    ldb = sqlite3.connect(":memory:", isolation_level=None)
    ldb.execute("PRAGMA journal_mode=WAL")
    ldb.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            age INTEGER NOT NULL,
            balance REAL NOT NULL,
            active INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    ldb.execute("CREATE INDEX idx_users_age ON users(age)")
    ldb.execute("CREATE INDEX idx_users_active ON users(active)")

    # --- Populate users ---
    s_insert = sdb.prepare("INSERT INTO users (id, name, email, age, balance, active, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)")

    user_rows = []
    for i in range(1, ROW_COUNT + 1):
        age = (seed_random(i) % 62) + 18
        balance = (seed_random(i * 7) % 100000) + (seed_random(i * 13) % 100) / 100
        active = 1 if seed_random(i * 3) % 10 < 7 else 0
        name = f"User_{i}"
        email = f"user{i}@example.com"
        user_rows.append([i, name, email, age, balance, active, "2024-01-01 00:00:00"])

    # SQLite bulk insert (explicit transaction for setup — not benchmarked)
    ldb.execute("BEGIN")
    ldb.executemany(
        "INSERT INTO users (id, name, email, age, balance, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        user_rows,
    )
    ldb.execute("COMMIT")

    # Stoolap bulk insert
    s_insert.execute_batch(user_rows)

    # ============================================================
    # CORE OPERATIONS
    # ============================================================
    print_header("CORE OPERATIONS")

    # --- SELECT by ID ---
    s_st = sdb.prepare("SELECT * FROM users WHERE id = $1")
    l_st = ldb.cursor()
    l_sql = "SELECT * FROM users WHERE id = ?"
    ids = [(i % ROW_COUNT) + 1 for i in range(ITERATIONS)]

    for i in range(WARMUP):
        s_st.query_one([ids[i]])
        l_st.execute(l_sql, (ids[i],)).fetchone()

    s_us = bench_us(lambda: [s_st.query_one([id_]) for id_ in ids], 1) / ITERATIONS
    l_us = bench_us(lambda: [l_st.execute(l_sql, (id_,)).fetchone() for id_ in ids], 1) / ITERATIONS
    print_row("SELECT by ID", s_us, l_us)

    # --- SELECT by index (exact) ---
    s_st = sdb.prepare("SELECT * FROM users WHERE age = $1")
    l_sql = "SELECT * FROM users WHERE age = ?"
    ages = [(i % 62) + 18 for i in range(ITERATIONS)]

    for i in range(WARMUP):
        s_st.query([ages[i]])
        l_st.execute(l_sql, (ages[i],)).fetchall()

    s_us = bench_us(lambda: [s_st.query([a]) for a in ages], 1) / ITERATIONS
    l_us = bench_us(lambda: [l_st.execute(l_sql, (a,)).fetchall() for a in ages], 1) / ITERATIONS
    print_row("SELECT by index (exact)", s_us, l_us)

    # --- SELECT by index (range) ---
    s_st = sdb.prepare("SELECT * FROM users WHERE age >= $1 AND age <= $2")
    l_sql = "SELECT * FROM users WHERE age >= ? AND age <= ?"

    for i in range(WARMUP):
        s_st.query([30, 40])
        l_st.execute(l_sql, (30, 40)).fetchall()

    s_us = bench_us(lambda: s_st.query([30, 40]), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql, (30, 40)).fetchall(), ITERATIONS)
    print_row("SELECT by index (range)", s_us, l_us)

    # --- SELECT complex ---
    s_st = sdb.prepare(
        "SELECT id, name, balance FROM users WHERE age >= 25 AND age <= 45 AND active = true ORDER BY balance DESC LIMIT 100"
    )
    l_sql = "SELECT id, name, balance FROM users WHERE age >= 25 AND age <= 45 AND active = 1 ORDER BY balance DESC LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("SELECT complex", s_us, l_us)

    # --- SELECT * (full scan) ---
    s_st = sdb.prepare("SELECT * FROM users")
    l_sql = "SELECT * FROM users"

    for i in range(WARMUP):
        s_st.query_raw()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query_raw(), ITERATIONS_HEAVY)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS_HEAVY)
    print_row("SELECT * (full scan)", s_us, l_us)

    # --- UPDATE by ID ---
    s_st = sdb.prepare("UPDATE users SET balance = $1 WHERE id = $2")
    l_sql = "UPDATE users SET balance = ? WHERE id = ?"
    update_params = [
        ((seed_random(i * 17) % 100000) + 0.5, (i % ROW_COUNT) + 1)
        for i in range(ITERATIONS)
    ]

    for i in range(WARMUP):
        s_st.execute(list(update_params[i]))
        ldb.execute(l_sql, update_params[i])

    s_us = bench_us(lambda: [s_st.execute(list(p)) for p in update_params], 1) / ITERATIONS
    def _l_update():
        for p in update_params:
            ldb.execute(l_sql, p)
    l_us = bench_us(_l_update, 1) / ITERATIONS
    print_row("UPDATE by ID", s_us, l_us)

    # --- UPDATE complex ---
    s_st = sdb.prepare("UPDATE users SET balance = $1 WHERE age >= $2 AND age <= $3 AND active = true")
    l_sql = "UPDATE users SET balance = ? WHERE age >= ? AND age <= ? AND active = 1"
    balances = [(seed_random(i * 23) % 100000) + 0.5 for i in range(ITERATIONS)]

    for i in range(WARMUP):
        s_st.execute([balances[i], 27, 28])
        ldb.execute(l_sql, (balances[i], 27, 28))

    s_us = bench_us(lambda: [s_st.execute([b, 27, 28]) for b in balances], 1) / ITERATIONS
    def _l_update_complex():
        for b in balances:
            ldb.execute(l_sql, (b, 27, 28))
    l_us = bench_us(_l_update_complex, 1) / ITERATIONS
    print_row("UPDATE complex", s_us, l_us)

    # --- INSERT single ---
    s_st = sdb.prepare(
        "INSERT INTO users (id, name, email, age, balance, active, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)"
    )
    l_sql = "INSERT INTO users (id, name, email, age, balance, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    base = ROW_COUNT + 1000

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        id_ = base + i
        s_st.execute([id_, f"New_{id_}", f"new{id_}@example.com", (seed_random(i * 29) % 62) + 18, 100.0, 1, "2024-01-01 00:00:00"])
    s_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        id_ = base + ITERATIONS + i
        ldb.execute(l_sql, (id_, f"New_{id_}", f"new{id_}@example.com", (seed_random(i * 29) % 62) + 18, 100.0, 1, "2024-01-01 00:00:00"))
    l_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS
    print_row("INSERT single", s_us, l_us)

    # --- DELETE by ID ---
    s_st = sdb.prepare("DELETE FROM users WHERE id = $1")
    l_sql = "DELETE FROM users WHERE id = ?"

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        s_st.execute([base + i])
    s_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        ldb.execute(l_sql, (base + ITERATIONS + i,))
    l_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS
    print_row("DELETE by ID", s_us, l_us)

    # --- DELETE complex ---
    s_st = sdb.prepare("DELETE FROM users WHERE age >= $1 AND age <= $2 AND active = true")
    l_sql = "DELETE FROM users WHERE age >= ? AND age <= ? AND active = 1"

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        s_st.execute([25, 26])
    s_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        ldb.execute(l_sql, (25, 26))
    l_us = ((time.perf_counter() - t0) * 1_000_000) / ITERATIONS
    print_row("DELETE complex", s_us, l_us)

    # --- Aggregation (GROUP BY) ---
    s_st = sdb.prepare("SELECT age, COUNT(*), AVG(balance) FROM users GROUP BY age")
    l_sql = "SELECT age, COUNT(*), AVG(balance) FROM users GROUP BY age"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS_MEDIUM)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS_MEDIUM)
    print_row("Aggregation (GROUP BY)", s_us, l_us)

    # ============================================================
    # ADVANCED OPERATIONS
    # ============================================================

    # Create orders table
    sdb.exec("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount FLOAT NOT NULL,
            status TEXT NOT NULL,
            order_date TEXT NOT NULL
        )
    """)
    sdb.exec("CREATE INDEX idx_orders_user_id ON orders(user_id)")
    sdb.exec("CREATE INDEX idx_orders_status ON orders(status)")

    ldb.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            order_date TEXT NOT NULL
        )
    """)
    ldb.execute("CREATE INDEX idx_orders_user_id ON orders(user_id)")
    ldb.execute("CREATE INDEX idx_orders_status ON orders(status)")

    # Populate orders (3 per user on average)
    s_order_insert = sdb.prepare(
        "INSERT INTO orders (id, user_id, amount, status, order_date) VALUES ($1, $2, $3, $4, $5)"
    )
    statuses = ["pending", "completed", "shipped", "cancelled"]
    order_rows = []
    for i in range(1, ROW_COUNT * 3 + 1):
        user_id = (seed_random(i * 11) % ROW_COUNT) + 1
        amount = (seed_random(i * 19) % 990) + 10 + (seed_random(i * 23) % 100) / 100
        status = statuses[seed_random(i * 31) % 4]
        order_rows.append([i, user_id, amount, status, "2024-01-15"])

    ldb.execute("BEGIN")
    ldb.executemany(
        "INSERT INTO orders (id, user_id, amount, status, order_date) VALUES (?, ?, ?, ?, ?)",
        order_rows,
    )
    ldb.execute("COMMIT")
    s_order_insert.execute_batch(order_rows)

    print_header("ADVANCED OPERATIONS")

    # --- INNER JOIN ---
    s_st = sdb.prepare(
        "SELECT u.name, o.amount FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE o.status = 'completed' LIMIT 100"
    )
    l_sql = "SELECT u.name, o.amount FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE o.status = 'completed' LIMIT 100"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("INNER JOIN", s_us, l_us)

    # --- LEFT JOIN + GROUP BY ---
    s_st = sdb.prepare(
        "SELECT u.name, COUNT(o.id) as order_count, SUM(o.amount) as total FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id, u.name LIMIT 100"
    )
    l_sql = "SELECT u.name, COUNT(o.id) as order_count, SUM(o.amount) as total FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id, u.name LIMIT 100"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("LEFT JOIN + GROUP BY", s_us, l_us)

    # --- Scalar subquery ---
    sql_s = "SELECT name, balance, (SELECT AVG(balance) FROM users) as avg_balance FROM users WHERE balance > (SELECT AVG(balance) FROM users) LIMIT 100"
    s_st = sdb.prepare(sql_s)
    l_sql = sql_s

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Scalar subquery", s_us, l_us)

    # --- IN subquery ---
    s_st = sdb.prepare(
        "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE status = 'completed') LIMIT 100"
    )
    l_sql = "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE status = 'completed') LIMIT 100"
    iters = 10

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("IN subquery", s_us, l_us)

    # --- EXISTS subquery ---
    s_st = sdb.prepare(
        "SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.amount > 500) LIMIT 100"
    )
    l_sql = "SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.amount > 500) LIMIT 100"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("EXISTS subquery", s_us, l_us)

    # --- CTE + JOIN ---
    s_st = sdb.prepare(
        "WITH high_value AS (SELECT user_id, SUM(amount) as total FROM orders GROUP BY user_id HAVING SUM(amount) > 1000) SELECT u.name, h.total FROM users u INNER JOIN high_value h ON u.id = h.user_id LIMIT 100"
    )
    l_sql = "WITH high_value AS (SELECT user_id, SUM(amount) as total FROM orders GROUP BY user_id HAVING SUM(amount) > 1000) SELECT u.name, h.total FROM users u INNER JOIN high_value h ON u.id = h.user_id LIMIT 100"
    iters = 20

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("CTE + JOIN", s_us, l_us)

    # --- Window ROW_NUMBER ---
    sql_common = "SELECT name, balance, ROW_NUMBER() OVER (ORDER BY balance DESC) as rank FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Window ROW_NUMBER", s_us, l_us)

    # --- Window ROW_NUMBER (PK) ---
    sql_common = "SELECT name, ROW_NUMBER() OVER (ORDER BY id) as rank FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Window ROW_NUMBER (PK)", s_us, l_us)

    # --- Window PARTITION BY ---
    sql_common = "SELECT name, age, balance, RANK() OVER (PARTITION BY age ORDER BY balance DESC) as age_rank FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Window PARTITION BY", s_us, l_us)

    # --- UNION ALL ---
    s_st = sdb.prepare(
        "SELECT name, 'high' as category FROM users WHERE balance > 50000 UNION ALL SELECT name, 'low' as category FROM users WHERE balance <= 50000 LIMIT 100"
    )
    l_sql = "SELECT name, 'high' as category FROM users WHERE balance > 50000 UNION ALL SELECT name, 'low' as category FROM users WHERE balance <= 50000 LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("UNION ALL", s_us, l_us)

    # --- CASE expression ---
    s_st = sdb.prepare(
        "SELECT name, CASE WHEN balance > 75000 THEN 'platinum' WHEN balance > 50000 THEN 'gold' WHEN balance > 25000 THEN 'silver' ELSE 'bronze' END as tier FROM users LIMIT 100"
    )
    l_sql = "SELECT name, CASE WHEN balance > 75000 THEN 'platinum' WHEN balance > 50000 THEN 'gold' WHEN balance > 25000 THEN 'silver' ELSE 'bronze' END as tier FROM users LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("CASE expression", s_us, l_us)

    # --- Complex JOIN+GROUP+HAVING ---
    s_st = sdb.prepare(
        "SELECT u.name, COUNT(DISTINCT o.id) as orders, SUM(o.amount) as total FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE u.active = true AND o.status IN ('completed', 'shipped') GROUP BY u.id, u.name HAVING COUNT(o.id) > 1 LIMIT 50"
    )
    l_sql = "SELECT u.name, COUNT(DISTINCT o.id) as orders, SUM(o.amount) as total FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE u.active = 1 AND o.status IN ('completed', 'shipped') GROUP BY u.id, u.name HAVING COUNT(o.id) > 1 LIMIT 50"
    iters = 20

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Complex JOIN+GRP+HAVING", s_us, l_us)

    # --- Batch INSERT (100 rows in transaction) ---
    iters = ITERATIONS
    base_id = ROW_COUNT * 10
    insert_sql = "INSERT INTO orders (id, user_id, amount, status, order_date) VALUES ($1, $2, $3, $4, $5)"
    l_insert_sql = "INSERT INTO orders (id, user_id, amount, status, order_date) VALUES (?, ?, ?, ?, ?)"

    t0 = time.perf_counter()
    for it in range(iters):
        batch = []
        for j in range(100):
            id_ = base_id + it * 100 + j
            batch.append([id_, 1, 100.0, "pending", "2024-02-01"])
        sdb.execute_batch(insert_sql, batch)
    s_us = ((time.perf_counter() - t0) * 1_000_000) / iters

    t0 = time.perf_counter()
    for it in range(iters):
        batch = []
        for j in range(100):
            id_ = base_id + iters * 100 + it * 100 + j
            batch.append((id_, 1, 100.0, "pending", "2024-02-01"))
        ldb.execute("BEGIN")
        ldb.executemany(l_insert_sql, batch)
        ldb.execute("COMMIT")
    l_us = ((time.perf_counter() - t0) * 1_000_000) / iters
    print_row("Batch INSERT (100 rows)", s_us, l_us)

    # ============================================================
    # BOTTLENECK HUNTERS
    # ============================================================
    print_header("BOTTLENECK HUNTERS")

    # --- DISTINCT (no ORDER) ---
    s_st = sdb.prepare("SELECT DISTINCT age FROM users")
    l_sql = "SELECT DISTINCT age FROM users"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("DISTINCT (no ORDER)", s_us, l_us)

    # --- DISTINCT + ORDER BY ---
    s_st = sdb.prepare("SELECT DISTINCT age FROM users ORDER BY age")
    l_sql = "SELECT DISTINCT age FROM users ORDER BY age"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("DISTINCT + ORDER BY", s_us, l_us)

    # --- COUNT DISTINCT ---
    s_st = sdb.prepare("SELECT COUNT(DISTINCT age) FROM users")
    l_sql = "SELECT COUNT(DISTINCT age) FROM users"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("COUNT DISTINCT", s_us, l_us)

    # --- LIKE prefix ---
    s_st = sdb.prepare("SELECT * FROM users WHERE name LIKE 'User_1%' LIMIT 100")
    l_sql = "SELECT * FROM users WHERE name LIKE 'User_1%' LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("LIKE prefix (User_1%)", s_us, l_us)

    # --- LIKE contains ---
    s_st = sdb.prepare("SELECT * FROM users WHERE email LIKE '%50%' LIMIT 100")
    l_sql = "SELECT * FROM users WHERE email LIKE '%50%' LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("LIKE contains (%50%)", s_us, l_us)

    # --- OR conditions ---
    s_st = sdb.prepare("SELECT * FROM users WHERE age = 25 OR age = 50 OR age = 75 LIMIT 100")
    l_sql = "SELECT * FROM users WHERE age = 25 OR age = 50 OR age = 75 LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("OR conditions (3 vals)", s_us, l_us)

    # --- IN list ---
    s_st = sdb.prepare("SELECT * FROM users WHERE age IN (20, 25, 30, 35, 40, 45, 50) LIMIT 100")
    l_sql = "SELECT * FROM users WHERE age IN (20, 25, 30, 35, 40, 45, 50) LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("IN list (7 values)", s_us, l_us)

    # --- NOT IN subquery ---
    s_st = sdb.prepare(
        "SELECT * FROM users WHERE id NOT IN (SELECT user_id FROM orders WHERE status = 'cancelled') LIMIT 100"
    )
    l_sql = "SELECT * FROM users WHERE id NOT IN (SELECT user_id FROM orders WHERE status = 'cancelled') LIMIT 100"
    iters = 10

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("NOT IN subquery", s_us, l_us)

    # --- NOT EXISTS subquery ---
    s_st = sdb.prepare(
        "SELECT * FROM users u WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.status = 'cancelled') LIMIT 100"
    )
    l_sql = "SELECT * FROM users u WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.status = 'cancelled') LIMIT 100"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("NOT EXISTS subquery", s_us, l_us)

    # --- OFFSET pagination ---
    s_st = sdb.prepare("SELECT * FROM users ORDER BY id LIMIT 100 OFFSET 5000")
    l_sql = "SELECT * FROM users ORDER BY id LIMIT 100 OFFSET 5000"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("OFFSET pagination (5000)", s_us, l_us)

    # --- Multi-column ORDER BY ---
    s_st = sdb.prepare("SELECT * FROM users ORDER BY age DESC, balance ASC, name LIMIT 100")
    l_sql = "SELECT * FROM users ORDER BY age DESC, balance ASC, name LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Multi-col ORDER BY (3)", s_us, l_us)

    # --- Self JOIN (same age) ---
    s_st = sdb.prepare(
        "SELECT u1.name, u2.name, u1.age FROM users u1 INNER JOIN users u2 ON u1.age = u2.age AND u1.id < u2.id LIMIT 100"
    )
    l_sql = "SELECT u1.name, u2.name, u1.age FROM users u1 INNER JOIN users u2 ON u1.age = u2.age AND u1.id < u2.id LIMIT 100"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Self JOIN (same age)", s_us, l_us)

    # --- Multi window funcs (3) ---
    sql_common = "SELECT name, balance, ROW_NUMBER() OVER (ORDER BY balance DESC) as rn, RANK() OVER (ORDER BY balance DESC) as rnk, LAG(balance) OVER (ORDER BY balance DESC) as prev_bal FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Multi window funcs (3)", s_us, l_us)

    # --- Nested subquery (3 levels) ---
    sql_common = "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)) LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common
    iters = 20

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Nested subquery (3 lvl)", s_us, l_us)

    # --- Multi aggregates (6) ---
    sql_common = "SELECT COUNT(*), SUM(balance), AVG(balance), MIN(balance), MAX(balance), COUNT(DISTINCT age) FROM users"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Multi aggregates (6)", s_us, l_us)

    # --- COALESCE + IS NOT NULL ---
    sql_common = "SELECT name, COALESCE(balance, 0) as bal FROM users WHERE balance IS NOT NULL LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("COALESCE + IS NOT NULL", s_us, l_us)

    # --- Expr in WHERE (funcs) ---
    s_st = sdb.prepare(
        "SELECT * FROM users WHERE LENGTH(name) > 7 AND UPPER(name) LIKE 'USER_%' LIMIT 100"
    )
    l_sql = "SELECT * FROM users WHERE LENGTH(name) > 7 AND UPPER(name) LIKE 'USER_%' LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Expr in WHERE (funcs)", s_us, l_us)

    # --- Math expressions ---
    sql_common = "SELECT name, balance * 1.1 as new_bal, ROUND(balance / 1000, 2) as k_bal, ABS(balance - 50000) as diff FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Math expressions", s_us, l_us)

    # --- String concat (||) ---
    sql_common = "SELECT name || ' (' || email || ')' as full_info FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("String concat (||)", s_us, l_us)

    # --- Large result (no LIMIT) ---
    s_st = sdb.prepare("SELECT id, name, balance FROM users WHERE active = true")
    l_sql = "SELECT id, name, balance FROM users WHERE active = 1"
    iters = 20

    for i in range(5):
        s_st.query_raw()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query_raw(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Large result (no LIMIT)", s_us, l_us)

    # --- Multiple CTEs (2) ---
    s_st = sdb.prepare(
        "WITH young AS (SELECT * FROM users WHERE age < 30), rich AS (SELECT * FROM users WHERE balance > 70000) SELECT y.name, r.name FROM young y INNER JOIN rich r ON y.id = r.id LIMIT 50"
    )
    l_sql = "WITH young AS (SELECT * FROM users WHERE age < 30), rich AS (SELECT * FROM users WHERE balance > 70000) SELECT y.name, r.name FROM young y INNER JOIN rich r ON y.id = r.id LIMIT 50"
    iters = 100

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Multiple CTEs (2)", s_us, l_us)

    # --- Correlated in SELECT ---
    s_st = sdb.prepare(
        "SELECT u.name, (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) as order_count FROM users u LIMIT 100"
    )
    l_sql = "SELECT u.name, (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) as order_count FROM users u LIMIT 100"
    iters = 100

    for i in range(5):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), iters)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), iters)
    print_row("Correlated in SELECT", s_us, l_us)

    # --- BETWEEN (non-indexed) ---
    s_st = sdb.prepare("SELECT * FROM users WHERE balance BETWEEN 25000 AND 75000 LIMIT 100")
    l_sql = "SELECT * FROM users WHERE balance BETWEEN 25000 AND 75000 LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("BETWEEN (non-indexed)", s_us, l_us)

    # --- GROUP BY (2 columns) ---
    s_st = sdb.prepare("SELECT age, active, COUNT(*), AVG(balance) FROM users GROUP BY age, active")
    l_sql = "SELECT age, active, COUNT(*), AVG(balance) FROM users GROUP BY age, active"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("GROUP BY (2 columns)", s_us, l_us)

    # --- CROSS JOIN (limited) ---
    s_st = sdb.prepare(
        "SELECT u.name, o.status FROM users u CROSS JOIN (SELECT DISTINCT status FROM orders) o LIMIT 100"
    )
    l_sql = "SELECT u.name, o.status FROM users u CROSS JOIN (SELECT DISTINCT status FROM orders) o LIMIT 100"

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("CROSS JOIN (limited)", s_us, l_us)

    # --- Derived table (FROM subquery) ---
    sql_common = "SELECT t.age_group, COUNT(*) FROM (SELECT CASE WHEN age < 30 THEN 'young' WHEN age < 50 THEN 'middle' ELSE 'senior' END as age_group FROM users) t GROUP BY t.age_group"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Derived table (FROM sub)", s_us, l_us)

    # --- Window ROWS frame ---
    sql_common = "SELECT name, balance, SUM(balance) OVER (ORDER BY balance ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING) as rolling_sum FROM users LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Window ROWS frame", s_us, l_us)

    # --- HAVING complex ---
    sql_common = "SELECT age FROM users GROUP BY age HAVING COUNT(*) > 100 AND AVG(balance) > 40000"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("HAVING complex", s_us, l_us)

    # --- Compare with subquery ---
    sql_common = "SELECT * FROM users WHERE balance > (SELECT AVG(amount) * 100 FROM orders) LIMIT 100"
    s_st = sdb.prepare(sql_common)
    l_sql = sql_common

    for i in range(WARMUP):
        s_st.query()
        l_st.execute(l_sql).fetchall()

    s_us = bench_us(lambda: s_st.query(), ITERATIONS)
    l_us = bench_us(lambda: l_st.execute(l_sql).fetchall(), ITERATIONS)
    print_row("Compare with subquery", s_us, l_us)

    # ============================================================
    # Summary
    # ============================================================
    print()
    print("=" * 80)
    print(f"SCORE: Stoolap {stoolap_wins} wins  |  SQLite {sqlite_wins} wins")
    print()
    print("NOTES:")
    print("- Both drivers use synchronous methods — fair comparison")
    print("- Stoolap: MVCC, parallel execution, columnar indexes")
    print("- SQLite: WAL mode, in-memory, stdlib driver")
    print("- Ratio > 1x = Stoolap faster  |  * = SQLite faster")
    print("=" * 80)

    sdb.close()
    ldb.close()


if __name__ == "__main__":
    main()
