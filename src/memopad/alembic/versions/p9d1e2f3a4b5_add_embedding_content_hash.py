"""Add content_hash column to the embedding table.

Revision ID: p9d1e2f3a4b5
Revises: o9c0d1e2f3a4
Create Date: 2026-08-04 10:00:00.000000

Why this exists: ``EmbeddingService.upsert_batch`` used to re-embed every item
on every call — re-indexing unchanged content burned the same CPU as the first
index. This migration adds a ``content_hash`` column (SHA-256 of the embedded
text) so ``upsert_batch`` can compare the text we're about to embed against the
text already embedded for a given ``(item_type, item_id)`` key and skip the
model call when nothing changed.

The column is nullable: pre-existing rows have no known hash, so on the first
post-migration ``upsert_batch`` they are treated as "changed" (NULL != hash),
re-embedded once, and then carry their hash going forward. The lazy
``_init_blob_store`` path and ``EmbeddingService._ensure_content_hash_column``
both backfill the column on databases that skip Alembic (some test fixtures),
so this migration is the primary path but not the only one.

This is a CPU-reduction change (part of the embedding perf fixes): unchanged
items no longer trigger ONNX inference, which dominates embedding cost.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "p9d1e2f3a4b5"
down_revision: Union[str, None] = "o9c0d1e2f3a4"
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


def upgrade() -> None:
    connection = op.get_bind()

    # The embedding table is created lazily by EmbeddingService when embeddings
    # are first used, so it may not exist yet on installs that never enabled
    # embeddings. In that case there's nothing to migrate — the lazy CREATE
    # already includes the column.
    if not table_exists(connection, "embedding"):
        return

    if _has_column(connection, "embedding", "content_hash"):
        return

    op.add_column(
        "embedding",
        sa.Column("content_hash", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Drop the content_hash column.

    Reverting drops the column; the next ``upsert_batch`` re-embeds everything
    once (NULL/missing hash == changed) and repopulates it on the next upgrade.
    """
    connection = op.get_bind()
    if not table_exists(connection, "embedding"):
        return
    if not _has_column(connection, "embedding", "content_hash"):
        return
    op.drop_column("embedding", "content_hash")