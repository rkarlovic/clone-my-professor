"""Drop chunk.sparse_embedding: the BGE-M3 sparse (hybrid search) experiment is removed.

It did not help on a second subject (2026-11-17) and was the one model call outside LiteLLM.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chunk DROP COLUMN IF EXISTS sparse_embedding")


def downgrade() -> None:
    op.execute("ALTER TABLE chunk ADD COLUMN sparse_embedding sparsevec(250002)")
