"""Add observation_schema table for category registry (noise gate)

Revision ID: i2c3d4e5f6a7
Revises: h1b2c3d4e5f6
Create Date: 2026-06-16 00:01:00.000000

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
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :table"
            ),
            {"table": table},
        )
        return result.fetchone() is not None
    else:
        result = connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table"),
            {"table": table},
        )
        return result.fetchone() is not None


def index_exists(connection, index_name: str) -> bool:
    """Check if an index exists (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    else:
        result = connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None


# revision identifiers, used by Alembic.
revision: str = "i2c3d4e5f6a7"
down_revision: Union[str, None] = "h1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create observation_schema table.

    Stores canonical category names per project with their aliases and usage
    frequency.  Used by SchemaService to normalise free-form [category] labels
    written by LLMs, implementing MemGraphRAG's Ontology Layer noise gate.

    MemoPad constraint: this table tracks derived index metadata only. It does not
    rewrite the markdown files that produced the observations.
    """
    connection = op.get_bind()

    if not table_exists(connection, "observation_schema"):
        op.create_table(
            "observation_schema",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("project.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("frequency", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "name", name="uq_obs_schema_project_name"),
        )

    if not index_exists(connection, "ix_obs_schema_project"):
        op.create_index("ix_obs_schema_project", "observation_schema", ["project_id"])


def downgrade() -> None:
    """Drop observation_schema table."""
    connection = op.get_bind()
    if index_exists(connection, "ix_obs_schema_project"):
        op.drop_index("ix_obs_schema_project", table_name="observation_schema")
    if table_exists(connection, "observation_schema"):
        op.drop_table("observation_schema")
