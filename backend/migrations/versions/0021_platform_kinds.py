"""Learning platforms of different kinds (Moodle, Microsoft Teams, Google Classroom).

- lti_platform.kind: moodle | teams | classroom (picks the material-sync connector)
- lti_platform.sync_config: non-secret sync settings (Teams: tenant_id, client_id; Classroom:
  the Workspace user the service account acts as). Secrets stay in environment variables whose
  *name* is in ws_token_env.
- platform_context_map.sync_course_id: the course id for material sync when it differs from
  the LTI context id (a Teams team's group id, a Classroom course id); NULL = the context id.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lti_platform
            ADD COLUMN kind varchar(20) NOT NULL DEFAULT 'moodle'
                CHECK (kind IN ('moodle', 'teams', 'classroom')),
            ADD COLUMN sync_config jsonb NOT NULL DEFAULT '{}';
        ALTER TABLE platform_context_map ADD COLUMN sync_course_id varchar(300);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE platform_context_map DROP COLUMN sync_course_id;
        ALTER TABLE lti_platform DROP COLUMN sync_config, DROP COLUMN kind;
    """)
