"""Answers students mark 👎 go to the teacher; follow-up questions in the chat.

- forward_reason gains `flagged`: the student marked the answer not helpful, so the question
  (with what the assistant said, `teacher_question.ai_answer`) goes to the teacher's queue. The
  teacher's answer with a source makes it part of the course's collected real-question set.
- chat_message.standalone: a follow-up ("a što je s drugim?") rewritten into a question that
  stands on its own, which is what the pipeline answered; NULL when the question was used as is.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE forward_reason ADD VALUE IF NOT EXISTS 'flagged'")
    op.execute("ALTER TABLE teacher_question ADD COLUMN ai_answer text")
    op.execute("ALTER TABLE chat_message ADD COLUMN standalone text")
    op.execute(
        "CREATE INDEX chat_message_student_time ON chat_message (kb_id, student_ref, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX chat_message_student_time")
    op.execute("ALTER TABLE chat_message DROP COLUMN standalone")
    op.execute("ALTER TABLE teacher_question DROP COLUMN ai_answer")
    # the enum value stays (Postgres cannot drop one in place); unused after the downgrade
