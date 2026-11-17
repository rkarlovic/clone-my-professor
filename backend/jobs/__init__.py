"""Background jobs. Code enqueues work only through the functions here (CLAUDE.md "Traffic and
scaling"): the queue behind them (Procrastinate today) can be swapped without touching callers.

Callers must have opened the queue once per process: `open_queue()` (the API does it at
startup) and `close_queue()` at shutdown.
"""

import uuid


def open_queue() -> None:
    from jobs.app import app

    app.open()


def close_queue() -> None:
    from jobs.app import app

    app.close()


def enqueue_ingest(kb_version_id: uuid.UUID, kb_id: uuid.UUID) -> int:
    """Queue processing of an uploaded draft version. One ingest per course runs at a time
    (lock), and the same version is never queued twice while waiting (queueing_lock)."""
    from jobs.tasks import ingest_version

    return ingest_version.configure(
        lock=f"kb:{kb_id}", queueing_lock=f"ingest:{kb_version_id}"
    ).defer(kb_version_id=str(kb_version_id))


def enqueue_checks(kb_version_id: uuid.UUID, kb_id: uuid.UUID) -> int:
    """Queue the check of a processed version (write check questions for new documents, score
    its search). Same per-course lock as ingest, so two checks never write questions for the
    same new document at once."""
    from jobs.tasks import check_version

    return check_version.configure(
        lock=f"kb:{kb_id}", queueing_lock=f"check:{kb_version_id}"
    ).defer(kb_version_id=str(kb_version_id))


def enqueue_persona_check(persona_id: uuid.UUID, kb_id: uuid.UUID) -> int:
    """Queue the activation check of a persona (answers a sample with it, then activates or
    rejects it)."""
    from jobs.tasks import check_persona

    return check_persona.configure(lock=f"kb:{kb_id}", queueing_lock=f"persona:{persona_id}").defer(
        persona_id=str(persona_id)
    )
