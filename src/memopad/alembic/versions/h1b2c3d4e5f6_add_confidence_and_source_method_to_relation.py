"""Add confidence and source_method columns to relation table

Revision ID: h1b2c3d4e5f6
Revises: g9a0b3c4d5e6
Create Date: 2026-05-04 12:00:00.000000

These two columns record *how certain* a relation is and *how it was created*.
Every user-authored [[wikilink]] gets confidence=1.0 / source_method='user_wikilink'.
Future AI-extraction passes (tree-sitter code analysis, LLM inference) will be
able to write lower confidence values and a different source_method so callers
can filter or down-weight speculative edges.

Both columns are nullable with database-level defaults so the migration never
needs to touch existing rows — they simply inherit the default on next write.
We also backfill the columns in SQL for clarity.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def column_exists(connection, table: str, column: str) -> bool:
    """Check whether a column exists (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.fetchone() is not None
    else:
        # SQLite
        result = connection.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in result]
        return column in columns


# revision identifiers
revision: str = "h1b2c3d4e5f6"
down_revision: Union[str, None] = "g9a0b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add confidence (Float) and source_method (String) to the relation table."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    # --- confidence column ---
    if not column_exists(connection, "relation", "confidence"):
        op.add_column(
            "relation",
            sa.Column("confidence", sa.Float(), nullable=True, server_default="1.0"),
        )
        # Backfill existing rows
        op.execute(text("UPDATE relation SET confidence = 1.0 WHERE confidence IS NULL"))

    # --- source_method column ---
    if not column_exists(connection, "relation", "source_method"):
        op.add_column(
            "relation",
            sa.Column(
                "source_method",
                sa.String(),
                nullable=True,
                server_default="user_wikilink",
            ),
        )
        # Backfill existing rows
        op.execute(
            text("UPDATE relation SET source_method = 'user_wikilink' WHERE source_method IS NULL")
        )

    # Optional index for filtering by source_method (e.g. "show only AI-extracted")
    # Only create when the dialect supports it efficiently (both do).
    _ = dialect  # suppress unused-variable warning; kept for future dialect branching


def downgrade() -> None:
    """Remove confidence and source_method from the relation table."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        if column_exists(connection, "relation", "source_method"):
            op.drop_column("relation", "source_method")
        if column_exists(connection, "relation", "confidence"):
            op.drop_column("relation", "confidence")
    else:
        # SQLite requires batch mode for DROP COLUMN
        with op.batch_alter_table("relation") as batch_op:
            if column_exists(connection, "relation", "source_method"):
                batch_op.drop_column("source_method")
            if column_exists(connection, "relation", "confidence"):
                batch_op.drop_column("confidence")
