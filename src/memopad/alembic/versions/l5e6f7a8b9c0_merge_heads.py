"""Merge the d7e8 and j3d4/k4 branches into a single head.

Revision ID: l5e6f7a8b9c0
Revises: ('d7e8f9a0b1c2', 'k4e5f6a7b8c9')
Create Date: 2026-06-26 00:02:00.000000

Why this exists: the earlier merge migration 6830751f5fb6 merged the a2b3 and
g9a0 branches, but subsequent migrations (h1b2 -> i2c3 -> j3d4) were then added
on top of g9a0b3c4d5e6 — one of that merge's *parents* — re-opening a branch
parallel to d7e8f9a0b1c2. That left two heads (d7e8f9a0b1c2, j3d4e5f6a7b8), so
`alembic upgrade head` raised "Multiple head revisions are present" and the CLI
`reset`/`reindex` paths (which call run_migrations -> upgrade head) could not
apply migrations. The embedding migration (k4e5f6a7b8c9, revises j3d4) added a
third head. This merge restores a single head so migrations apply again.

This is a pure branch merge: no schema changes.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "l5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = ("d7e8f9a0b1c2", "k4e5f6a7b8c9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op merge of the d7e8 and k4 branches."""
    pass


def downgrade() -> None:
    """Merge migrations are not reversible."""
    pass