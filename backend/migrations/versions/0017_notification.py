"""In-app notifications (build step 4): one row per recipient.

A recipient is a teacher (`teacher_id`) or a student of a course (`student_ref`, the same opaque
reference as in chat_message; rule 9). `kind` + `data` say what happened; `read_at` marks it seen.
No e-mail: CMP stores no e-mail addresses.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notification (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            teacher_id uuid REFERENCES teacher(id) ON DELETE CASCADE,
            student_ref varchar(200),
            kind varchar(40) NOT NULL,
            data jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            read_at timestamptz,
            CHECK ((teacher_id IS NULL) <> (student_ref IS NULL))
        );
        CREATE INDEX notification_teacher ON notification (teacher_id, read_at);
        CREATE INDEX notification_student ON notification (kb_id, student_ref, read_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE notification")
