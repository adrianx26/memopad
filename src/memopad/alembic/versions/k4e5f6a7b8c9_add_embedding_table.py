"""Add embedding table for semantic/hybrid search.

Revision ID: k4e5f6a7b8c9
Revises: j3d4e5f6a7b8
Create Date: 2026-06-26 00:01:00.000000

The embedding table stores one vector per entity for cosine-similarity search.
It is created lazily by EmbeddingService.init_store() on first write; this
migration ensures it also exists immediately after a fresh `memopad reset`
(without waiting for the first indexed note) and shows up in schema inspections.

The schema here mirrors EmbeddingService.init_store exactly (no foreign keys,
matching the lazy-create path) so the two never drift.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


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


# revision identifiers, used by Alembic.
revision: str = "k4e5f6a7b8c9"
down_revision: Union[str, None] = "j3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the embedding table and its project/model index."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if not table_exists(connection, "embedding"):
        if dialect == "postgresql":
            # Postgres: use a timestamptz default. Vectors are stored as BYTEA
            # (packed float32) to match the SQLite BLOB path the service uses.
            op.create_table(
                "embedding",
                sa.Column("entity_id", sa.Integer(), primary_key=True),
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
            )
        else:
            # SQLite: mirror EmbeddingService.init_store verbatim, including the
            # datetime('now') default, so the lazy-create and migration agree.
            op.create_table(
                "embedding",
                sa.Column("entity_id", sa.Integer(), primary_key=True),
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
            )

    if not index_exists(connection, "ix_embedding_project_model"):
        op.create_index(
            "ix_embedding_project_model", "embedding", ["project_id", "model"]
        )


def downgrade() -> None:
    """Drop the embedding table and index."""
    connection = op.get_bind()
    if index_exists(connection, "ix_embedding_project_model"):
        op.drop_index("ix_embedding_project_model", table_name="embedding")
    if table_exists(connection, "embedding"):
        op.drop_table("embedding")