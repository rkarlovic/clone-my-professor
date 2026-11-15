"""Forward reason `failed_check`: the live answer check rejected the answer (rule 1).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE forward_reason ADD VALUE IF NOT EXISTS 'failed_check'")


def downgrade() -> None:
    # Postgres cannot drop an enum value; rebuild the type without it.
    op.execute("""
        UPDATE teacher_question SET reason = 'no_source' WHERE reason = 'failed_check';
        ALTER TYPE forward_reason RENAME TO forward_reason_old;
        CREATE TYPE forward_reason AS ENUM ('admin', 'out_of_scope', 'low_score', 'no_source');
        ALTER TABLE teacher_question
            ALTER COLUMN reason TYPE forward_reason USING reason::text::forward_reason;
        DROP TYPE forward_reason_old;
    """)
