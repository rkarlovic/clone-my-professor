"""Several teachers per course: `course_teacher` (build step 4).

Every teacher on the list may manage the course (materials, approval, queue, settings).
`role` is "owner" (who created it; `knowledge_base.teacher_id` keeps pointing there) or
"teacher"; `added_via` says how they joined ("owner", "lti": an instructor of a linked LMS
course, vouched for by the LMS). Existing owners are copied in.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE course_teacher (
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            teacher_id uuid NOT NULL REFERENCES teacher(id) ON DELETE CASCADE,
            role varchar(20) NOT NULL DEFAULT 'teacher',
            added_via varchar(20) NOT NULL DEFAULT 'owner',
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (kb_id, teacher_id)
        );
        INSERT INTO course_teacher (kb_id, teacher_id, role, added_via)
            SELECT id, teacher_id, 'owner', 'owner' FROM knowledge_base;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE course_teacher")
