"""Add reindex_state table for incremental reindex fingerprinting.

Revision ID: o9c0d1e2f3a4
Revises: n8b9c0d1e2f3
Create Date: 2026-08-03 00:02:00.000000

Stores one row per (project_id, entity_id) recording the fingerprint of the
entity's last indexed output and the index schema version it was built under.
``SearchService.reindex_all`` consults this to skip entities whose indexed
content is unchanged since the last reindex, re-index only changed/new
entities, and prune entries for entities that no longer exist — avoiding a
full wipe-and-rebuild on every call.

The table has no foreign keys (mirroring the ``embedding`` table): deletion
is reconciled manually by the incremental reindex pass. A ``REINDEX_INDEX_VERSION``
bump in ``search_service`` forces every row to be treated as stale and
re-indexed, which is how future FTS schema / tokenizer changes trigger a
clean rebuild without dropping the table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def table_exists(connection, table: str) -> bool:
    """Check if a table exists."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :table"
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
revision: str = "o9c0d1e2f3a4"
down_revision: Union[str, None] = "n8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create reindex_state table."""
    connection = op.get_bind()

    if not table_exists(connection, "reindex_state"):
        op.create_table(
            "reindex_state",
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("fingerprint", sa.String(), nullable=False),
            sa.Column("index_version", sa.Integer(), nullable=False),
            sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("project_id", "entity_id", name="pk_reindex_state"),
        )

    if not index_exists(connection, "ix_reindex_state_project"):
        op.create_index("ix_reindex_state_project", "reindex_state", ["project_id"])


def downgrade() -> None:
    """Drop reindex_state table."""
    connection = op.get_bind()
    if index_exists(connection, "ix_reindex_state_project"):
        op.drop_index("ix_reindex_state_project", table_name="reindex_state")
    if table_exists(connection, "reindex_state"):
        op.drop_table("reindex_state")