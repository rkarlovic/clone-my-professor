"""The Procrastinate app (Postgres-based job queue). Only jobs/ knows the queue is Procrastinate;
the rest of the code goes through jobs/__init__.py, so the queue could be replaced.

Worker: uv run python -m scripts.worker
"""

from procrastinate import App, PsycopgConnector

from core.settings import get_settings

INGEST_QUEUE = "ingest"  # heavy (parsing, embedding): one job at a time per worker

app = App(
    connector=PsycopgConnector(conninfo=get_settings().jobs_conninfo),
    import_paths=["jobs.tasks"],
)
