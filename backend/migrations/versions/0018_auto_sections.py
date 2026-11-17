"""Auto-add from selected LMS sections (CLAUDE.md "Connectors": per-course rule "always ask me"
(default) or "auto-add from selected sections").

- `lms_file.section`: the LMS section a file was last seen in.
- `platform_context_map.auto_add_sections`: sections whose new or changed files are published
  without review; empty = always ask (the default).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lms_file ADD COLUMN section varchar(500) NOT NULL DEFAULT '';
        ALTER TABLE platform_context_map
            ADD COLUMN auto_add_sections text[] NOT NULL DEFAULT '{}';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE platform_context_map DROP COLUMN auto_add_sections;
        ALTER TABLE lms_file DROP COLUMN section;
    """)
