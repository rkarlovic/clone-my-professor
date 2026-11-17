"""Versions that remove files (build step 4, part 3).

- `kb_version.removed_files`: file names this version leaves out of its base version (a teacher
  removing a file, or the LMS sync removing a file that is gone from every linked LMS course).
- `kb_version.auto_activate`: published as soon as it is processed, without the teacher
  (only for removals the LMS sync makes: taking material away, never adding any; rule 5 is
  about what *enters* a live version).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version
            ADD COLUMN removed_files jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN auto_activate boolean NOT NULL DEFAULT false;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE kb_version DROP COLUMN auto_activate, DROP COLUMN removed_files")
