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

"""Type mapping and conversion tests."""

import json
from datetime import datetime, timezone, timedelta

from stoolap import Database


def test_integer_types():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_int (val INTEGER)")

    db.execute("INSERT INTO t_int VALUES ($1)", [0])
    db.execute("INSERT INTO t_int VALUES ($1)", [42])
    db.execute("INSERT INTO t_int VALUES ($1)", [-100])
    db.execute("INSERT INTO t_int VALUES ($1)", [2**53])  # large integer

    rows = db.query("SELECT val FROM t_int ORDER BY val")
    assert rows[0]["val"] == -100
    assert rows[1]["val"] == 0
    assert rows[2]["val"] == 42
    assert rows[3]["val"] == 2**53
    db.close()


def test_float_types():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_float (val FLOAT)")

    db.execute("INSERT INTO t_float VALUES ($1)", [0.0])
    db.execute("INSERT INTO t_float VALUES ($1)", [3.14159])
    db.execute("INSERT INTO t_float VALUES ($1)", [-1.5])
    db.execute("INSERT INTO t_float VALUES ($1)", [1e10])

    rows = db.query("SELECT val FROM t_float ORDER BY val")
    assert abs(rows[0]["val"] - (-1.5)) < 0.001
    assert abs(rows[1]["val"] - 0.0) < 0.001
    assert abs(rows[2]["val"] - 3.14159) < 0.001
    assert abs(rows[3]["val"] - 1e10) < 0.001
    db.close()


def test_text_types():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_text (val TEXT)")

    db.execute("INSERT INTO t_text VALUES ($1)", [""])
    db.execute("INSERT INTO t_text VALUES ($1)", ["hello world"])
    db.execute("INSERT INTO t_text VALUES ($1)", ["unicode: \u00e9\u00e0\u00fc\u00f1"])
    db.execute("INSERT INTO t_text VALUES ($1)", ["emoji: \U0001f600"])

    rows = db.query("SELECT val FROM t_text ORDER BY rowid")
    assert rows[0]["val"] == ""
    assert rows[1]["val"] == "hello world"
    assert rows[2]["val"] == "unicode: \u00e9\u00e0\u00fc\u00f1"
    assert rows[3]["val"] == "emoji: \U0001f600"
    db.close()


def test_boolean_type():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_bool (val BOOLEAN)")

    db.execute("INSERT INTO t_bool VALUES ($1)", [True])
    db.execute("INSERT INTO t_bool VALUES ($1)", [False])

    rows = db.query("SELECT val FROM t_bool ORDER BY rowid")
    assert rows[0]["val"] is True
    assert rows[1]["val"] is False
    db.close()


def test_null_type():
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_null (id INTEGER PRIMARY KEY, val TEXT)")

    db.execute("INSERT INTO t_null VALUES ($1, $2)", [1, None])

    row = db.query_one("SELECT val FROM t_null WHERE id = $1", [1])
    assert row["val"] is None
    db.close()


def test_datetime_utc():
    """UTC datetime roundtrips correctly."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_ts (id INTEGER PRIMARY KEY, ts TIMESTAMP)")

    # Use explicit UTC timezone for reliable roundtrip
    dt = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
    db.execute("INSERT INTO t_ts VALUES ($1, $2)", [1, dt])

    row = db.query_one("SELECT ts FROM t_ts WHERE id = $1", [1])
    ts = row["ts"]
    assert isinstance(ts, datetime)
    assert ts.year == 2024
    assert ts.month == 6
    assert ts.day == 15
    assert ts.hour == 12
    assert ts.minute == 30
    assert ts.second == 45
    db.close()


def test_datetime_timezone_aware():
    """Timezone-aware datetime converted to UTC."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_tstz (id INTEGER PRIMARY KEY, ts TIMESTAMP)")

    # Create a datetime in UTC+5
    tz = timezone(timedelta(hours=5))
    dt = datetime(2024, 6, 15, 17, 30, 0, tzinfo=tz)  # 17:30 UTC+5 = 12:30 UTC
    db.execute("INSERT INTO t_tstz VALUES ($1, $2)", [1, dt])

    row = db.query_one("SELECT ts FROM t_tstz WHERE id = $1", [1])
    ts = row["ts"]
    assert isinstance(ts, datetime)
    assert ts.hour == 12  # Converted to UTC
    assert ts.minute == 30
    db.close()


def test_json_dict():
    """Python dict serialized as JSON."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_json_dict (id INTEGER PRIMARY KEY, data JSON)")

    data = {"name": "Alice", "age": 30, "tags": ["admin", "user"]}
    db.execute("INSERT INTO t_json_dict VALUES ($1, $2)", [1, data])

    row = db.query_one("SELECT data FROM t_json_dict WHERE id = $1", [1])
    # JSON comes back as a string
    parsed = json.loads(row["data"])
    assert parsed["name"] == "Alice"
    assert parsed["age"] == 30
    assert parsed["tags"] == ["admin", "user"]
    db.close()


def test_json_list():
    """Python list serialized as JSON."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_json_list (id INTEGER PRIMARY KEY, data JSON)")

    data = [1, "two", 3.0, True, None]
    db.execute("INSERT INTO t_json_list VALUES ($1, $2)", [1, data])

    row = db.query_one("SELECT data FROM t_json_list WHERE id = $1", [1])
    parsed = json.loads(row["data"])
    assert parsed == [1, "two", 3.0, True, None]
    db.close()


def test_mixed_types_in_query():
    """Query returning multiple column types."""
    db = Database.open(":memory:")
    db.exec("""
        CREATE TABLE t_mixed (
            id INTEGER PRIMARY KEY,
            name TEXT,
            score FLOAT,
            active BOOLEAN,
            note TEXT
        )
    """)
    db.execute(
        "INSERT INTO t_mixed VALUES ($1, $2, $3, $4, $5)",
        [1, "Alice", 95.5, True, None],
    )

    row = db.query_one("SELECT * FROM t_mixed WHERE id = $1", [1])
    assert isinstance(row["id"], int)
    assert isinstance(row["name"], str)
    assert isinstance(row["score"], float)
    assert isinstance(row["active"], bool)
    assert row["note"] is None
    db.close()


def test_bool_not_confused_with_int():
    """Bool params must not be treated as int (bool is subclass of int in Python)."""
    db = Database.open(":memory:")
    db.exec("CREATE TABLE t_boolcheck (id INTEGER PRIMARY KEY, flag BOOLEAN)")
    db.execute("INSERT INTO t_boolcheck VALUES ($1, $2)", [1, True])
    db.execute("INSERT INTO t_boolcheck VALUES ($1, $2)", [2, False])

    rows = db.query("SELECT * FROM t_boolcheck ORDER BY id")
    assert rows[0]["flag"] is True
    assert rows[1]["flag"] is False
    # They should be bool, not int 1/0
    assert type(rows[0]["flag"]) is bool
    assert type(rows[1]["flag"]) is bool
    db.close()
