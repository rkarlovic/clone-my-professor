"""chat_message: every student question and the clone's reply (build step 3).

One row per exchange, for the student's history, the teacher queue link and analytics. Holds
student text, so it is personal data (rule 9): `student_ref` is an opaque id, never a name;
retention and teacher-level access arrive with the dashboard. `feedback` is the student's
👍 (1) / 👎 (-1), the start of the collected real-question set.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE chat_message (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            kb_version_id uuid NOT NULL REFERENCES kb_version(id),
            student_ref varchar(200) NOT NULL,
            question text NOT NULL,
            answer text NOT NULL,
            forwarded forward_reason,
            teacher_question_id uuid REFERENCES teacher_question(id),
            citations jsonb NOT NULL DEFAULT '[]',
            trace jsonb NOT NULL DEFAULT '{}',
            seconds jsonb NOT NULL DEFAULT '{}',
            feedback smallint CHECK (feedback IN (-1, 1)),
            feedback_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX chat_message_student ON chat_message (kb_id, student_ref, created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE chat_message")
