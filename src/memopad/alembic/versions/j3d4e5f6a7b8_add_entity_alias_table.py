"""Add entity_alias table for frontmatter aliases.

Revision ID: j3d4e5f6a7b8
Revises: i2c3d4e5f6a7
Create Date: 2026-06-16 00:02:00.000000

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
revision: str = "j3d4e5f6a7b8"
down_revision: Union[str, None] = "i2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create entity_alias table.

    Stores explicit aliases declared in markdown frontmatter so LinkResolver can
    resolve WikiLinks through user-authored alternate names.

    MemoPad constraint: aliases are exact user-authored mappings. This migration
    does not add fuzzy entity matching or automatic entity merging.
    """
    connection = op.get_bind()

    if not table_exists(connection, "entity_alias"):
        op.create_table(
            "entity_alias",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "entity_id",
                sa.Integer(),
                sa.ForeignKey("entity.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("project.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("alias", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False, server_default="frontmatter"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not index_exists(connection, "ix_entity_alias_entity"):
        op.create_index("ix_entity_alias_entity", "entity_alias", ["entity_id"])
    if not index_exists(connection, "ix_entity_alias_project_alias"):
        op.create_index(
            "ix_entity_alias_project_alias",
            "entity_alias",
            ["project_id", "alias"],
        )


def downgrade() -> None:
    """Drop entity_alias table."""
    connection = op.get_bind()
    if index_exists(connection, "ix_entity_alias_project_alias"):
        op.drop_index("ix_entity_alias_project_alias", table_name="entity_alias")
    if index_exists(connection, "ix_entity_alias_entity"):
        op.drop_index("ix_entity_alias_entity", table_name="entity_alias")
    if table_exists(connection, "entity_alias"):
        op.drop_table("entity_alias")
