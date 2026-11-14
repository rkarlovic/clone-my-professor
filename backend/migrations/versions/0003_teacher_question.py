"""Teacher question queue: questions the clone forwarded instead of answering (rule 2).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One queue per knowledge base (it outlives versions); kb_version_id records which version
    # could not answer. student_ref is an opaque reference, never a name or e-mail (rule 9).
    # The app role's default grants cover the new table. No row-level security yet: the queue is
    # read per kb_id by the teacher dashboard (build step 3), which adds teacher-level access.
    op.execute("""
        CREATE TYPE teacher_question_status AS ENUM ('open', 'answered', 'dismissed');
        CREATE TYPE forward_reason AS ENUM ('admin', 'out_of_scope', 'low_score', 'no_source');

        CREATE TABLE teacher_question (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            kb_version_id uuid REFERENCES kb_version(id),
            student_ref varchar(200),
            question text NOT NULL,
            reason forward_reason NOT NULL,
            status teacher_question_status NOT NULL DEFAULT 'open',
            answer text,
            created_at timestamptz NOT NULL DEFAULT now(),
            answered_at timestamptz
        );
        CREATE INDEX ix_teacher_question_kb_status ON teacher_question (kb_id, status);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS teacher_question;
        DROP TYPE IF EXISTS forward_reason;
        DROP TYPE IF EXISTS teacher_question_status;
    """)
