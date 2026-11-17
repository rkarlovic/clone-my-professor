"""Ingest state on kb_version, for uploads processed by a background job (build step 3).

`status` stays the approval state (draft | approved | active, rule 5); `ingest_status` says
whether the draft's material has been processed yet (queued | processing | ready | failed).
`pending_files` lists the uploaded files the job still has to process; `base_version_id` is the
version whose unchanged documents are carried over. Versions made before this revision were
ingested by script and are `ready`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE ingest_status AS ENUM ('queued', 'processing', 'ready', 'failed');
        ALTER TABLE kb_version
            ADD COLUMN ingest_status ingest_status NOT NULL DEFAULT 'ready',
            ADD COLUMN ingest_error text,
            ADD COLUMN ingest_report jsonb,
            ADD COLUMN pending_files jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN base_version_id uuid REFERENCES kb_version(id),
            ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
        ALTER TABLE kb_version ALTER COLUMN ingest_status SET DEFAULT 'queued';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version
            DROP COLUMN created_at,
            DROP COLUMN base_version_id,
            DROP COLUMN pending_files,
            DROP COLUMN ingest_report,
            DROP COLUMN ingest_error,
            DROP COLUMN ingest_status;
        DROP TYPE ingest_status;
    """)
