"""Teacher answers in the question queue: who answered, and the sources they pointed to.

`answer_sources` ([{"file_name", "page"}]) turns a forwarded question the teacher answers into
a question + source pair: the start of the course's collected real-question set (CLAUDE.md
"Evaluation strategy"), at no extra work for the teacher.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE teacher_question
            ADD COLUMN answer_sources jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN answered_by uuid REFERENCES teacher(id);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE teacher_question DROP COLUMN answered_by, DROP COLUMN answer_sources;
    """)
