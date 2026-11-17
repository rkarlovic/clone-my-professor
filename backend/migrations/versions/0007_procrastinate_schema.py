"""Procrastinate's job-queue tables, functions and types (background jobs, build step 3).

Applied from the installed procrastinate package's own schema.sql, so a fresh install gets the
schema of the pinned version. **When upgrading procrastinate**, add a new revision that applies
the package's migration files (procrastinate/sql/migrations/) between the old and new version;
never re-run this one. The app role's default grants cover the new tables and sequences;
functions and types are usable by PUBLIC.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Raw driver SQL (SQLAlchemy text() would read `:name` as params); psycopg still reads `%`
    # as a placeholder, so escape it the way procrastinate's own apply_schema does.
    op.get_bind().exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS procrastinate_events, procrastinate_periodic_defers,
            procrastinate_jobs, procrastinate_workers, procrastinate_version CASCADE;
        DROP FUNCTION IF EXISTS procrastinate_notify_queue_job_inserted CASCADE;
    """)
    op.get_bind().exec_driver_sql("""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                     JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate%%'
            LOOP EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE'; END LOOP;
            FOR r IN SELECT t.typname FROM pg_type t
                     JOIN pg_namespace n ON n.oid = t.typnamespace
                     WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate%%'
                       AND t.typtype IN ('e', 'c', 'd')
            LOOP EXECUTE 'DROP TYPE IF EXISTS ' || quote_ident(r.typname) || ' CASCADE'; END LOOP;
        END $$;
    """)
