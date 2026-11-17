"""Auto-generated check questions in the database, and each version's check result.

`check_question`: a student-style question written by the model from one slide of a course
document (CLAUDE.md "Evaluation strategy"). It belongs to the course and to that document's
content (`file_name` + `file_hash`): it is valid for every version that contains the same file,
so carried-over documents keep their questions and only new or changed ones get new questions.
The teacher may delete a question (`deleted_at`); deleted questions are kept so regeneration
does not bring them back.

`kb_version.check_status` / `check_report`: the background check of a processed version (its
search scored on the valid questions), shown to the teacher when reviewing a draft.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE check_question (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            question text NOT NULL,
            style varchar(30) NOT NULL,
            file_name varchar(500) NOT NULL,
            file_hash varchar(64) NOT NULL,
            page integer NOT NULL,
            created_for_version_id uuid REFERENCES kb_version(id) ON DELETE SET NULL,
            trace jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        );
        CREATE INDEX check_question_kb ON check_question (kb_id, file_name, file_hash);
        ALTER TABLE kb_version
            ADD COLUMN check_status varchar(20),
            ADD COLUMN check_report jsonb;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version DROP COLUMN check_report, DROP COLUMN check_status;
        DROP TABLE check_question;
    """)
