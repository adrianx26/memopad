"""Key embedding vectors by item type so observations and relations are embedded too.

Revision ID: m6f7a8b9c0d1
Revises: l5e6f7a8b9c0
Create Date: 2026-06-27 00:01:00.000000

The original embedding table (k4e5f6a7b8c9) keyed vectors on `entity_id` alone, so only
entities could be embedded and semantic/hybrid search was filtered to ENTITY rows. To let
observations (facts) and relations participate in semantic search we rekey the table by
`(item_type, item_id)`:

    item_type IN ('entity', 'observation', 'relation')
    item_id   = entity.id / observation.id / relation.id

Ids collide across those three tables, so the composite key is required. Existing rows
(there are none in practice — the feature never ran before `fastembed` was installed, so
`is_enabled()` was always False and no upsert ever wrote a vector) are carried over as
`item_type='entity'` with `entity_id` renamed to `item_id`.

The dialect-aware pattern mirrors k4e5f6a7b8c9: SQLite uses `datetime('now')`, Postgres
uses `timestamptz`. SQLite table rebuilds go through Alembic batch mode (render_as_batch
is enabled in env.py). Idempotent: a no-op if the table already has the new shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "m6f7a8b9c0d1"
down_revision: Union[str, None] = "l5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(connection, table: str) -> bool:
    """Check if a table exists (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = :table"
            ),
            {"table": table},
        )
        return result.fetchone() is not None
    result = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table"),
        {"table": table},
    )
    return result.fetchone() is not None


def index_exists(connection, index_name: str) -> bool:
    """Check if an index exists."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    result = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


def _has_column(connection, table: str, column: str) -> bool:
    """True if `table` already has `column` (used to detect the new vs old schema)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.fetchone() is not None
    result = connection.execute(text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result.fetchall())


def _create_new_table(connection) -> None:
    """Create the embedding table in the new (item_type, item_id) shape."""
    dialect = connection.dialect.name
    if dialect == "postgresql":
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


def upgrade() -> None:
    """Rekey the embedding table by (item_type, item_id); create if absent."""
    connection = op.get_bind()

    if not table_exists(connection, "embedding"):
        _create_new_table(connection)
    elif _has_column(connection, "embedding", "item_type"):
        # Already migrated to the new shape — nothing to do.
        pass
    else:
        # Old shape: PK on entity_id, no item_type. Rebuild preserving rows as entities.
        if connection.dialect.name == "postgresql":
            # Drop the old entity_id PK, rename to item_id, add item_type, new composite PK.
            op.drop_constraint("embedding_pkey", "embedding", type_="primary")
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
            # SQLite: batch-rebuild the table (render_as_batch is on in env.py).
            with op.batch_alter_table("embedding", recreate="always") as batch_op:
                batch_op.alter_column(
                    "entity_id",
                    new_column_name="item_id",
                    existing_type=sa.Integer(),
                    nullable=False,
                )
                batch_op.add_column(
                    sa.Column(
                        "item_type", sa.String(), nullable=False, server_default="entity"
                    )
                )
                batch_op.create_primary_key("pk_embedding", ["item_type", "item_id"])

    # (Re)create the project/model index idempotently.
    if index_exists(connection, "ix_embedding_project_model"):
        op.drop_index("ix_embedding_project_model", table_name="embedding")
    op.create_index(
        "ix_embedding_project_model", "embedding", ["project_id", "model"]
    )


def downgrade() -> None:
    """Drop the embedding table and index (the old single-PK shape is not restored).

    Restoring the entity_id-only shape would discard observation/relation vectors, so we
    drop entirely — the table is recreated lazily by EmbeddingService in the new shape on
    next use, or by a fresh `alembic upgrade head`.
    """
    connection = op.get_bind()
    if index_exists(connection, "ix_embedding_project_model"):
        op.drop_index("ix_embedding_project_model", table_name="embedding")
    if table_exists(connection, "embedding"):
        op.drop_table("embedding")