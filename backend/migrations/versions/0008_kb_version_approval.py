"""Approval and activation of kb_versions (rule 5): who approved, when, and when activated.

`status`: draft (not reviewed) -> approved (reviewed; may be activated, also what a formerly
active version goes back to, so it can be re-activated as a rollback) -> active (what students
get; at most one per knowledge base, kb_version_one_active).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version
            ADD COLUMN approved_at timestamptz,
            ADD COLUMN approved_by uuid REFERENCES teacher(id),
            ADD COLUMN activated_at timestamptz;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version
            DROP COLUMN activated_at,
            DROP COLUMN approved_by,
            DROP COLUMN approved_at;
    """)
