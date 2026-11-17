"""Approval and activation of knowledge-base versions (rule 5: nothing reaches students without
the teacher's approval).

    draft --approve--> approved --activate--> active
      |                    ^                    |
      +--discard           +-----(demoted)------+   activating another version

Students only ever get `active_version` (at most one per course, enforced by the
kb_version_one_active index). Re-activating an older approved version is the rollback.
Every change locks the course row, so two requests cannot interleave.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.db import scope_transaction
from core.models import (
    Chunk,
    Document,
    IngestStatus,
    KbVersion,
    KbVersionStatus,
    KnowledgeBase,
)


class ApprovalError(ValueError):
    """The version is not in a state that allows this step; the message says why."""


def _lock(session: Session, version: KbVersion) -> None:
    session.execute(
        select(KnowledgeBase.id).where(KnowledgeBase.id == version.kb_id).with_for_update()
    )
    session.refresh(version)  # its state may have changed while we waited for the lock


def active_version(session: Session, kb_id: uuid.UUID) -> KbVersion | None:
    """The version students get, or None while the teacher has activated nothing."""
    return session.scalars(
        select(KbVersion).where(
            KbVersion.kb_id == kb_id, KbVersion.status == KbVersionStatus.active
        )
    ).first()


def approve_version(session: Session, version: KbVersion, teacher_id: uuid.UUID) -> KbVersion:
    """The teacher has reviewed a processed draft (its files, sections and summary)."""
    _lock(session, version)
    if version.status != KbVersionStatus.draft:
        raise ApprovalError(f"version {version.version} is already {version.status.value}")
    if version.ingest_status != IngestStatus.ready:
        raise ApprovalError(
            f"version {version.version} is {version.ingest_status.value}; "
            "only a processed version can be approved"
        )
    now = datetime.now(UTC)
    version.status = KbVersionStatus.approved
    version.approved_at, version.approved_by = now, teacher_id
    scope_transaction(session, version.id)  # same transaction as the status change
    session.execute(update(Document).values(approved_at=now))
    session.flush()
    return version


def activate_version(session: Session, version: KbVersion) -> KbVersion:
    """Make an approved version the one students get; the previous active version goes back to
    `approved` (so it can be re-activated). One transaction: students never see none or two."""
    _lock(session, version)
    if version.status == KbVersionStatus.active:
        raise ApprovalError(f"version {version.version} is already active")
    if version.status != KbVersionStatus.approved:
        raise ApprovalError(f"version {version.version} must be approved before it is activated")
    current = active_version(session, version.kb_id)
    if current is not None:
        current.status = KbVersionStatus.approved
        session.flush()  # free the one-active slot before taking it
    version.status = KbVersionStatus.active
    version.activated_at = datetime.now(UTC)
    session.flush()
    return version


def discard_version(session: Session, version: KbVersion) -> None:
    """Delete a draft the teacher does not want (its documents and chunks; the stored files
    stay, they are shared by content). Approved or active versions are kept as history."""
    _lock(session, version)
    if version.status != KbVersionStatus.draft:
        raise ApprovalError(
            f"version {version.version} is {version.status.value}; only a draft can be discarded"
        )
    if version.ingest_status in (IngestStatus.queued, IngestStatus.processing):
        raise ApprovalError(f"version {version.version} is still being processed")
    building_on_it = session.scalar(
        select(KbVersion.version).where(
            KbVersion.base_version_id == version.id,
            KbVersion.ingest_status.in_([IngestStatus.queued, IngestStatus.processing]),
        )
    )
    if building_on_it:
        raise ApprovalError(
            f"version {building_on_it} is being built from version {version.version}; "
            "wait until it is processed"
        )
    scope_transaction(session, version.id)  # same transaction as deleting the version row
    session.query(Chunk).filter(Chunk.parent_id.is_not(None)).delete()
    session.query(Chunk).delete()
    session.query(Document).delete()
    session.execute(
        update(KbVersion)
        .where(KbVersion.base_version_id == version.id)
        .values(base_version_id=None)  # processed later versions already hold their copies
    )
    session.delete(version)
    session.flush()
