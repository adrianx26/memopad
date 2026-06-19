"""Add conflict detection fields to observation table

Revision ID: h1b2c3d4e5f6
Revises: g9a0b3c4d5e6
Create Date: 2026-06-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def column_exists(connection, table: str, column: str) -> bool:
    """Check if a column exists in a table (idempotent migration support)."""
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
        result = connection.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in result]
        return column in columns


# revision identifiers, used by Alembic.
revision: str = "h1b2c3d4e5f6"
down_revision: Union[str, None] = "g9a0b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add conflict tracking columns to the observation table.

    These columns support the MemGraphRAG-inspired conflict detection feature:
    - conflict_score:      0.0–1.0 float; higher = more divergent from sibling observation
    - conflicting_obs_id:  FK to the other observation in a conflicting pair (nullable)
    - conflict_resolved:   True once a human or LLM has explicitly settled the conflict
    - provenance_path:     file_path of the document that produced this observation

    MemoPad constraint: these are derived quality fields on the DB index. They do
    not rewrite markdown source files.
    """
    connection = op.get_bind()

    with op.batch_alter_table("observation", schema=None) as batch_op:
        if not column_exists(connection, "observation", "conflict_score"):
            batch_op.add_column(sa.Column("conflict_score", sa.Float(), nullable=True))
        if not column_exists(connection, "observation", "conflicting_obs_id"):
            batch_op.add_column(
                sa.Column("conflicting_obs_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_observation_conflicting_obs_id",
                "observation",
                ["conflicting_obs_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if not column_exists(connection, "observation", "conflict_resolved"):
            batch_op.add_column(
                sa.Column(
                    "conflict_resolved",
                    sa.Boolean(),
                    nullable=False,
                    server_default="0",
                )
            )
        if not column_exists(connection, "observation", "provenance_path"):
            batch_op.add_column(sa.Column("provenance_path", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove conflict tracking columns from observation table."""
    with op.batch_alter_table("observation", schema=None) as batch_op:
        try:
            batch_op.drop_constraint("fk_observation_conflicting_obs_id", type_="foreignkey")
        except Exception:
            pass
        batch_op.drop_column("provenance_path")
        batch_op.drop_column("conflict_resolved")
        batch_op.drop_column("conflicting_obs_id")
        batch_op.drop_column("conflict_score")
