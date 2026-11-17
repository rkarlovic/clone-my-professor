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
    _queue_checks(uuid.UUID(kb_version_id))
    if report:
        log.info(
            "kb_version %s: %d documents processed, %d carried over, %d chunks, %.0f s",
            kb_version_id,
            report.documents,
            len(report.carried_over),
            report.chunks_embedded,
            report.seconds,
        )


def _queue_checks(version_id: uuid.UUID) -> None:
    """After processing, queue the version's check; a failure here must not fail the ingest
    (the version is processed and usable either way)."""
    from sqlalchemy.orm import Session

    from core.db import app_engine
    from core.models import KbVersion
    from jobs import enqueue_checks

    try:
        with Session(app_engine()) as s, s.begin():
            v = s.get(KbVersion, version_id)
            v.check_status = "queued"
            kb_id = v.kb_id
        enqueue_checks(version_id, kb_id)
    except Exception:
        log.exception("could not queue the check of kb_version %s", version_id)


@app.task(name="check_version", queue=INGEST_QUEUE, pass_context=False)
def check_version(kb_version_id: str) -> None:
    """Write check questions for the version's new documents and score its search."""
    from core.checks import check_version as run

    report = run(uuid.UUID(kb_version_id))
    log.info(
        "kb_version %s check: %s/%s found in top 5, %d questions written, %.0f s",
        kb_version_id,
        report["found_top5"],
        report["questions"],
        report["written"],
        report["seconds"],
    )
