"""Initial knowledge-base schema with row-level security per kb_version.

Revision ID: 0001
Revises:
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rows are visible/writable only inside a transaction that set app.kb_version_id
# (see core.db.kb_scope). Unset or empty setting -> NULL -> no rows at all.
_KB_FILTER = "kb_version_id = NULLIF(current_setting('app.kb_version_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TYPE kb_version_status AS ENUM ('draft', 'approved', 'active');
        CREATE TYPE content_type AS ENUM ('definition', 'example', 'exercise', 'solution', 'other');

        CREATE TABLE teacher (
            id uuid PRIMARY KEY,
            name varchar(200) NOT NULL,
            institution_id varchar(100),
            consent_voice boolean NOT NULL DEFAULT false,
            consent_avatar boolean NOT NULL DEFAULT false
        );

        CREATE TABLE knowledge_base (
            id uuid PRIMARY KEY,
            teacher_id uuid NOT NULL REFERENCES teacher(id),
            subject_name varchar(300) NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'active'
        );

        CREATE TABLE kb_version (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id),
            version integer NOT NULL,
            status kb_version_status NOT NULL DEFAULT 'draft',
            UNIQUE (kb_id, version)
        );
        -- At most one active version per knowledge base (activation is atomic).
        CREATE UNIQUE INDEX kb_version_one_active ON kb_version (kb_id) WHERE status = 'active';

        CREATE TABLE document (
            id uuid PRIMARY KEY,
            kb_version_id uuid NOT NULL REFERENCES kb_version(id),
            source_platform varchar(30) NOT NULL DEFAULT 'upload',
            external_id varchar(300),
            file_name varchar(500) NOT NULL,
            file_hash varchar(64) NOT NULL,
            modified_at timestamptz,
            approved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_document_kb_version_id ON document (kb_version_id);

        CREATE TABLE chunk (
            id uuid PRIMARY KEY,
            kb_version_id uuid NOT NULL REFERENCES kb_version(id),
            document_id uuid NOT NULL REFERENCES document(id),
            parent_id uuid REFERENCES chunk(id),
            text text NOT NULL,
            context text,
            heading_path text,
            content_type content_type NOT NULL DEFAULT 'other',
            page_or_slide integer,
            week_topic varchar(200),
            embedding vector(1024),
            sparse_embedding sparsevec(250002)
        );
        CREATE INDEX ix_chunk_kb_version_id ON chunk (kb_version_id);
        CREATE INDEX ix_chunk_document_id ON chunk (document_id);
        -- HNSW + kb_version filter: use pgvector iterative scans (hnsw.iterative_scan)
        -- at query time so filtering does not starve results.
        CREATE INDEX ix_chunk_embedding_hnsw ON chunk USING hnsw (embedding vector_cosine_ops);
    """)
    for table in ("document", "chunk"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY kb_version_isolation ON {table} "
            f"USING ({_KB_FILTER}) WITH CHECK ({_KB_FILTER})"
        )


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS chunk;
        DROP TABLE IF EXISTS document;
        DROP TABLE IF EXISTS kb_version;
        DROP TABLE IF EXISTS knowledge_base;
        DROP TABLE IF EXISTS teacher;
        DROP TYPE IF EXISTS content_type;
        DROP TYPE IF EXISTS kb_version_status;
    """)
