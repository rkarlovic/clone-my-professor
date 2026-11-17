"""persona_config: the teacher's settings for how the clone talks (instruction layer 2).

Versioned per course. `status`: draft (being edited, can be previewed) -> checking (the
activation check job runs) -> active (what the chat uses; at most one per course) | rejected
(the check found too few forwarded or answered questions; `check_report` says which).
A previously active persona becomes `retired`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE persona_config (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            version integer NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'draft',
            settings jsonb NOT NULL DEFAULT '{}',
            free_text text NOT NULL DEFAULT '',
            created_by uuid REFERENCES teacher(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            approved_at timestamptz,
            activated_at timestamptz,
            check_report jsonb,
            UNIQUE (kb_id, version)
        );
        CREATE UNIQUE INDEX persona_one_active ON persona_config (kb_id) WHERE status = 'active';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE persona_config")
