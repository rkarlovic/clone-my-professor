"""Record the other pages a chunk's content repeats on (duplicates folded at ingestion).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows get '{}': nothing was folded before this revision. Row-level security and
    # the app role's grants are per table, so they cover the new column unchanged.
    op.execute("ALTER TABLE chunk ADD COLUMN also_on_pages integer[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE chunk DROP COLUMN also_on_pages")
