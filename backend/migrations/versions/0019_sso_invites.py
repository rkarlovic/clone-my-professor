"""Teacher sign-in with Microsoft/Google and course invites (build step 4).

`sso_login_state`: one OIDC sign-in round trip (state, nonce, PKCE verifier), single-use.
`course_invite`: a single-use link that adds a signed-in teacher to a course without an LMS;
only the code's sha256 is stored.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sso_login_state (
            state varchar(100) PRIMARY KEY,
            nonce varchar(100) NOT NULL,
            code_verifier varchar(100) NOT NULL,
            provider varchar(20) NOT NULL,
            next_page varchar(100) NOT NULL DEFAULT 'teacher',
            created_at timestamptz NOT NULL DEFAULT now(),
            used_at timestamptz
        );
        CREATE TABLE course_invite (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            code_hash varchar(64) NOT NULL UNIQUE,
            created_by uuid REFERENCES teacher(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            used_by uuid REFERENCES teacher(id) ON DELETE SET NULL
        );
        CREATE INDEX course_invite_kb ON course_invite (kb_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE course_invite; DROP TABLE sso_login_state")
