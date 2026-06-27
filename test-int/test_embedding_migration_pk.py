"""Regression test for the embedding composite-PK migration bug.

Runs the REAL Alembic migration chain (``alembic upgrade head``) on a fresh
SQLite DB — NOT ``Base.metadata.create_all`` — so it exercises the migration DDL
that ships to real installs. The embeddings integration test creates the table
via the ORM model (which already has the correct composite PK), so it cannot
catch a bug in the migration itself.

Background: m6f7a8b9c0d1's SQLite batch rebuild left ``PRIMARY KEY (item_id)``
instead of ``(item_type, item_id)``, which broke
``EmbeddingService.upsert_batch``'s ``INSERT ... ON CONFLICT(item_type, item_id)
DO UPDATE`` (no matching constraint) and also made the key wrong on its own,
since entity/observation/relation ids collide across tables. The fix (m6f7a8b9c0d1
SQLite else-branch rewritten + repair migration n8b9c0d1e2f3) must produce the
composite PK on a fresh install.

The upgrade runs with a synchronous ``sqlite://`` URL so alembic/env.py takes
its sync online path (no aiosqlite worker thread, no nest_asyncio loop nesting).
"""

import sqlite3
from pathlib import Path

import memopad
from alembic import command
from alembic.config import Config as AlembicConfig

ALEMBIC_DIR = Path(memopad.__file__).parent / "alembic"


def _alembic_config(db_url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _pk_columns(conn, table: str) -> list[str]:
    # PRAGMA table_info row: (cid, name, type, notnull, dflt, pk). pk is 0 for
    # non-PK columns and 1,2,... for PK columns in order.
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[5] != 0]


def test_migration_chain_produces_composite_embedding_pk(tmp_path):
    """Fresh-install migration chain must yield PRIMARY KEY (item_type, item_id)."""
    db = tmp_path / "fresh_install.db"
    command.upgrade(_alembic_config(f"sqlite:///{db.as_posix()}"), "head")

    conn = sqlite3.connect(str(db))
    try:
        assert "embedding" in [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        assert set(_pk_columns(conn, "embedding")) == {"item_type", "item_id"}, (
            f"embedding PK is {_pk_columns(conn, 'embedding')!r}, expected (item_type, item_id)"
        )
    finally:
        conn.close()


def test_embedding_upsert_handles_colliding_ids(tmp_path):
    """Entity#N and observation#N must coexist; ON CONFLICT(item_type,item_id) must work."""
    db = tmp_path / "upsert.db"
    command.upgrade(_alembic_config(f"sqlite:///{db.as_posix()}"), "head")

    conn = sqlite3.connect(str(db))
    try:
        upsert = (
            "INSERT INTO embedding (item_type, item_id, project_id, model, dim, vector, updated_at) "
            "VALUES (:t, :i, 1, 'm', 4, :v, datetime('now')) "
            "ON CONFLICT(item_type, item_id) DO UPDATE SET vector = excluded.vector"
        )
        # Same numeric id across item types — would collide under PK(item_id) alone.
        conn.execute(upsert, {"t": "entity", "i": 1, "v": b"\xaa\xbb\xcc\xdd"})
        conn.execute(upsert, {"t": "observation", "i": 1, "v": b"\x11\x22\x33\x44"})
        conn.execute(upsert, {"t": "relation", "i": 1, "v": b"\xee\xff\x00\x99"})
        conn.commit()

        rows = conn.execute(
            "SELECT item_type, item_id FROM embedding ORDER BY item_type"
        ).fetchall()
        assert rows == [("entity", 1), ("observation", 1), ("relation", 1)], rows

        # Re-upserting the same key updates in place (no duplicate).
        conn.execute(upsert, {"t": "entity", "i": 1, "v": b"\x00\x00\x00\x00"})
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM embedding").fetchone()[0] == 3
    finally:
        conn.close()
