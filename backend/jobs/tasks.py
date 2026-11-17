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


@app.task(name="check_persona", queue=INGEST_QUEUE, pass_context=False)
def check_persona(persona_id: str) -> None:
    """Answer a sample of check questions and the generic forward questions with a persona;
    activate it if forwarding and answering stay above the thresholds, else reject it."""
    from core.persona import check_persona as run

    report = run(uuid.UUID(persona_id))
    log.info(
        "persona %s: %s (forwarded %s/%s, answered %s/%s, %.0f s)",
        persona_id,
        "active" if report["passed"] else "rejected",
        report.get("forwarded"),
        report.get("generic"),
        report.get("answered"),
        report.get("check_questions"),
        report.get("seconds", 0),
    )


@app.task(name="sync_course", queue=INGEST_QUEUE, pass_context=False)
def sync_course(map_id: str) -> None:
    """Look for new or changed files in a linked LMS course; changes become a draft version."""
    from core.sync import sync_course as run

    report = run(uuid.UUID(map_id))
    log.info("sync of link %s: %s", map_id, report.get("status"))


@app.periodic(cron="*/20 * * * *")
@app.task(name="poll_lms", queue=INGEST_QUEUE, pass_context=False)
def poll_lms(timestamp: int) -> None:
    """Every 20 minutes: queue a sync for each linked course whose teacher turned sync on."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from core.db import app_engine
    from core.models import PlatformContextMap
    from jobs import enqueue_sync

    with Session(app_engine()) as s:
        links = s.execute(
            select(PlatformContextMap.id, PlatformContextMap.kb_id).where(
                PlatformContextMap.sync_enabled
            )
        ).all()
    for map_id, kb_id in links:
        enqueue_sync(map_id, kb_id)
    log.info("LMS poll: %d linked course(s) queued", len(links))
