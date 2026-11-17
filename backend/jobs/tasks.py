"""Job bodies. Each is a thin wrapper: the work itself lives in the core/ingestion modules."""

import logging
import uuid

from jobs.app import INGEST_QUEUE, app

log = logging.getLogger(__name__)


@app.task(name="ingest_version", queue=INGEST_QUEUE, pass_context=False)
def ingest_version(kb_version_id: str) -> None:
    """Process an uploaded draft version (carry-over + new files). A failure marks the version
    `failed` with the error; the teacher can retry, which queues this job again."""
    from ingestion.pipeline import process_version  # heavy imports (Docling) only in workers

    report = process_version(uuid.UUID(kb_version_id))
    if report:
        log.info(
            "kb_version %s: %d documents processed, %d carried over, %d chunks, %.0f s",
            kb_version_id,
            report.documents,
            len(report.carried_over),
            report.chunks_embedded,
            report.seconds,
        )
