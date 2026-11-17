"""LMS material sync (build step 4, part 2): Web Services access per platform, sync settings
and state per linked course, the files last seen, and where a version came from.

- `lti_platform.ws_base_url` / `ws_token_env`: the LMS's Web Services address and the *name* of
  the environment variable that holds the token (secrets never in the database).
- `platform_context_map.sync_enabled` (off until the teacher turns it on) and the last sync's
  time, status and report.
- `lms_file`: each file seen in a linked LMS course (name, size, modification time, content
  hash), so unchanged files are not downloaded again.
- `kb_version.origin`: "upload" (the teacher's own) or "lms" (found by the sync).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lti_platform
            ADD COLUMN ws_base_url varchar(500),
            ADD COLUMN ws_token_env varchar(100);
        ALTER TABLE platform_context_map
            ADD COLUMN sync_enabled boolean NOT NULL DEFAULT false,
            ADD COLUMN last_sync_at timestamptz,
            ADD COLUMN last_sync_status varchar(20),
            ADD COLUMN last_sync_report jsonb;
        CREATE TABLE lms_file (
            id uuid PRIMARY KEY,
            map_id uuid NOT NULL REFERENCES platform_context_map(id) ON DELETE CASCADE,
            external_id varchar(1000) NOT NULL,
            file_name varchar(500) NOT NULL,
            filesize bigint NOT NULL,
            timemodified bigint NOT NULL,
            file_hash varchar(64),
            seen_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (map_id, external_id)
        );
        ALTER TABLE kb_version ADD COLUMN origin varchar(20) NOT NULL DEFAULT 'upload';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE kb_version DROP COLUMN origin;
        DROP TABLE lms_file;
        ALTER TABLE platform_context_map
            DROP COLUMN last_sync_report, DROP COLUMN last_sync_status,
            DROP COLUMN last_sync_at, DROP COLUMN sync_enabled;
        ALTER TABLE lti_platform DROP COLUMN ws_token_env, DROP COLUMN ws_base_url;
    """)
