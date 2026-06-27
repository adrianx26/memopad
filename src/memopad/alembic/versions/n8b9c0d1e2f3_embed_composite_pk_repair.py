"""Repair: ensure the embedding table PRIMARY KEY is (item_type, item_id).

Revision ID: n8b9c0d1e2f3
Revises: m6f7a8b9c0d1
Create Date: 2026-06-27 14:30:00.000000

Why this exists: the previous migration (m6f7a8b9c0d1) rebuilt the `embedding`
table on SQLite via ``op.batch_alter_table(..., recreate="always")`` followed by
``batch_op.create_primary_key("pk_embedding", ["item_type", "item_id"])``. On
SQLite, batch mode *reflects* the existing single-column primary key
(``entity_id``, renamed to ``item_id``) into the recreated table, and a
subsequent ``create_primary_key`` does not replace it — it is a no-op against
an already-present PK. The net result was a table with ``PRIMARY KEY (item_id)``
and an ``item_type`` column, but **no** composite unique constraint.

That breaks every write path: ``EmbeddingService.upsert_batch`` runs
``INSERT ... ON CONFLICT(item_type, item_id) DO UPDATE ...``, which raises
"ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint". It
also makes the key incorrect on its own — entity, observation, and relation ids
collide across tables, so ``item_id`` alone cannot be unique.

This migration repairs any DB that applied the buggy m6f7a8b9c0d1 by rebuilding
the table with the intended ``PRIMARY KEY (item_type, item_id)``. It is a no-op
on DBs that already have the correct composite PK (including Postgres, where
m6f7a8b9c0d1's non-batch path produced the right PK). Existing rows are
preserved; rows lacking ``item_type`` are carried over as ``'entity'``.

The rebuild uses an explicit SQLite table swap rather than
``batch_alter_table`` + ``create_primary_key`` to avoid the no-op that caused
this bug. (m6f7a8b9c0d1's own SQLite else-branch is fixed separately so fresh
DBs no longer produce the buggy shape.)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "n8b9c0d1e2f3"
down_revision: Union[str, None] = "m6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(connection, table: str) -> bool:
    if connection.dialect.name == "postgresql":
        return (
            connection.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
                {"t": table},
            ).fetchone()
            is not None
        )
    return (
        connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).fetchone()
        is not None
    )


def _has_column(connection, table: str, column: str) -> bool:
    if connection.dialect.name == "postgresql":
        return (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            ).fetchone()
            is not None
        )
    return any(
        row[1] == column
        for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    )


def _pk_columns(connection, table: str) -> list[str]:
    """Return the ordered PK column names for `table`."""
    if connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = :t::regclass AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)"
            ),
            {"t": table},
        ).fetchall()
        return [r[0] for r in rows]
    # SQLite: pk order is in the 6th field of PRAGMA table_info (0 = not in PK).
    return [
        row[1]
        for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if row[5] != 0
    ]


def _create_new_table(connection) -> None:
    """Create the embedding table with PRIMARY KEY (item_type, item_id)."""
    if connection.dialect.name == "postgresql":
        op.create_table(
            "embedding",
            sa.Column("item_type", sa.String(), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("dim", sa.Integer(), nullable=False),
            sa.Column("vector", sa.LargeBinary(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("item_type", "item_id", name="pk_embedding"),
        )
    else:
        op.create_table(
            "embedding",
            sa.Column("item_type", sa.String(), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("dim", sa.Integer(), nullable=False),
            sa.Column("vector", sa.LargeBinary(), nullable=False),
            sa.Column(
                "updated_at",
                sa.String(),
                nullable=False,
                server_default=text("datetime('now')"),
            ),
            sa.PrimaryKeyConstraint("item_type", "item_id", name="pk_embedding"),
        )


def _rebuild_with_composite_pk(connection) -> None:
    """Rebuild the embedding table with the composite PK, preserving rows.

    Handles the old shape (entity_id PK, no item_type) and the buggy shape
    (item_id PK with item_type). SQLite uses an explicit table swap because
    batch_alter_table's create_primary_key is a no-op against a reflected PK.
    """
    has_item_type = _has_column(connection, "embedding", "item_type")
    id_col = "item_id" if _has_column(connection, "embedding", "item_id") else "entity_id"
    has_rows = connection.execute(text("SELECT COUNT(*) FROM embedding")).scalar() > 0

    if has_rows:
        type_expr = "item_type" if has_item_type else "'entity'"
        connection.execute(
            text(
                f"CREATE TABLE _embedding_pk_repair_backup AS "
                f"SELECT {type_expr} AS item_type, {id_col} AS item_id, "
                f"project_id, model, dim, vector, updated_at FROM embedding"
            )
        )
    op.drop_table("embedding")
    _create_new_table(connection)
    if has_rows:
        connection.execute(
            text(
                "INSERT INTO embedding "
                "(item_type, item_id, project_id, model, dim, vector, updated_at) "
                "SELECT item_type, item_id, project_id, model, dim, vector, updated_at "
                "FROM _embedding_pk_repair_backup"
            )
        )
        connection.execute(text("DROP TABLE _embedding_pk_repair_backup"))


def upgrade() -> None:
    connection = op.get_bind()

    if not table_exists(connection, "embedding"):
        # Nothing to repair (EmbeddingService creates lazily in the new shape).
        return

    if set(_pk_columns(connection, "embedding")) == {"item_type", "item_id"}:
        # Already has the correct composite PK — nothing to do.
        return

    if connection.dialect.name == "postgresql":
        # m6f7a8b9c0d1's Postgres path produced the correct composite PK, so this
        # branch is only reachable if something else left a single-column PK.
        pk_name = connection.execute(
            text(
                "SELECT i.relname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "WHERE c.relname = 'embedding' AND i.indisprimary"
            )
        ).scalar()
        if pk_name:
            op.drop_constraint(pk_name, "embedding", type_="primary")
        if not _has_column(connection, "embedding", "item_type"):
            op.alter_column(
                "embedding",
                "entity_id",
                new_column_name="item_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
            op.add_column(
                "embedding",
                sa.Column("item_type", sa.String(), nullable=False, server_default="entity"),
            )
        op.create_primary_key("pk_embedding", "embedding", ["item_type", "item_id"])
    else:
        _rebuild_with_composite_pk(connection)


def downgrade() -> None:
    """No-op: the buggy single-PK shape is not worth restoring.

    The composite PK is correct; downgrading would reintroduce the bug. The
    `embedding` table can be dropped entirely by m6f7a8b9c0d1's downgrade if a
    full rollback is ever needed, and recreated lazily in the correct shape.
    """
    pass
