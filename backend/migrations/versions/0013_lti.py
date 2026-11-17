"""LTI 1.3 (build step 4): registered platforms, login states, LMS course -> CMP course links,
and an opaque external key for teachers who arrive from an LMS.

- `lti_platform`: one row per LMS registration (issuer + client id), with its OIDC login URL
  and public key set URL; `deployment_ids` empty = accept any deployment of that client.
- `lti_login_state`: the OIDC third-party-initiated login round trip (state, nonce), short-lived
  and single-use; a used or expired state is refused (replay protection).
- `platform_context_map`: many LMS courses -> one knowledge base (CLAUDE.md data model).
- `teacher.external_ref`: sha256 of (issuer, user id) for teachers from an LMS. The LMS user id
  itself is not stored, nor names or e-mails (rule 9).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE lti_platform (
            id uuid PRIMARY KEY,
            name varchar(200) NOT NULL,
            issuer varchar(500) NOT NULL,
            client_id varchar(200) NOT NULL,
            auth_login_url varchar(500) NOT NULL,
            jwks_url varchar(500) NOT NULL,
            deployment_ids text[] NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (issuer, client_id)
        );
        CREATE TABLE lti_login_state (
            state varchar(100) PRIMARY KEY,
            nonce varchar(100) NOT NULL,
            platform_id uuid NOT NULL REFERENCES lti_platform(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            used_at timestamptz
        );
        CREATE TABLE platform_context_map (
            id uuid PRIMARY KEY,
            platform_id uuid NOT NULL REFERENCES lti_platform(id),
            context_id varchar(300) NOT NULL,
            context_title varchar(500),
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (platform_id, context_id)
        );
        ALTER TABLE teacher ADD COLUMN external_ref varchar(64) UNIQUE;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE teacher DROP COLUMN external_ref;
        DROP TABLE platform_context_map;
        DROP TABLE lti_login_state;
        DROP TABLE lti_platform;
    """)
