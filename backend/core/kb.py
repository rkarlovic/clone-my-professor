"""Knowledge bases and their versions. Every ingest creates a new *draft* version (rule 5);
activation (draft -> active) is a separate, teacher-approved step in build step 3."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.db import kb_scope
from core.models import Document, IngestStatus, KbVersion, KbVersionStatus, KnowledgeBase, Teacher
from core.storage import StoredFile


class NothingChanged(ValueError):
    """Every uploaded file is already in the course, unchanged: no new version is made."""


class StillProcessing(RuntimeError):
    """An earlier upload of this course is still queued or processing; a new one would build
    on an older version and silently drop that upload's files."""


SPIKE_TEACHER = "spike"  # placeholder owner for the retrieval spike; no personal data


def get_or_create_kb(session: Session, subject: str) -> KnowledgeBase:
    kb = session.scalars(select(KnowledgeBase).where(KnowledgeBase.subject_name == subject)).first()
    if kb:
        return kb
    teacher = session.scalars(select(Teacher).where(Teacher.name == SPIKE_TEACHER)).first()
    if not teacher:
        teacher = Teacher(name=SPIKE_TEACHER)
        session.add(teacher)
        session.flush()
    from core.access import create_course

    return create_course(session, teacher.id, subject)


def create_draft_version(session: Session, kb_id: uuid.UUID) -> KbVersion:
    latest = session.scalar(select(func.max(KbVersion.version)).where(KbVersion.kb_id == kb_id))
    version = KbVersion(kb_id=kb_id, version=(latest or 0) + 1, status=KbVersionStatus.draft)
    session.add(version)
    session.flush()
    return version


def find_version(session: Session, subject: str, version: int | None = None) -> KbVersion:
    """A specific version of a subject, or its newest one."""
    q = (
        select(KbVersion)
        .join(KnowledgeBase, KnowledgeBase.id == KbVersion.kb_id)
        .where(KnowledgeBase.subject_name == subject)
    )
    q = q.where(KbVersion.version == version) if version else q.order_by(KbVersion.version.desc())
    found = session.scalars(q).first()
    if not found:
        raise LookupError(f"no knowledge-base version for subject {subject!r} (version={version})")
    return found


def base_version(session: Session, kb_id: uuid.UUID) -> KbVersion | None:
    """What a new upload builds on. A processed draft newer than the active version holds the
    teacher's latest uploads (not reviewed yet), so uploads stack on the newest such draft;
    otherwise the active version (also after a rollback to an older one); with neither, the
    newest processed version."""
    versions = session.scalars(
        select(KbVersion).where(KbVersion.kb_id == kb_id).order_by(KbVersion.version.desc())
    ).all()
    active = next((v for v in versions if v.status == KbVersionStatus.active), None)
    ready = [v for v in versions if v.ingest_status == IngestStatus.ready]
    newer_drafts = [
        v
        for v in ready
        if v.status == KbVersionStatus.draft and (active is None or v.version > active.version)
    ]
    if newer_drafts:
        return newer_drafts[0]
    if active:
        return active
    return ready[0] if ready else None


def _lock_idle(session: Session, kb_id: uuid.UUID) -> None:
    """Lock the course; refuse while a version is queued or processing (a new one would build
    on an older version and drop that one's changes)."""
    session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == kb_id).with_for_update())
    busy = session.scalar(
        select(KbVersion.version).where(
            KbVersion.kb_id == kb_id,
            KbVersion.ingest_status.in_([IngestStatus.queued, IngestStatus.processing]),
        )
    )
    if busy:
        raise StillProcessing(f"version {busy} of this course is still being processed")


def create_upload_version(
    session: Session, kb: KnowledgeBase, files: Sequence[StoredFile], origin: str = "upload"
) -> KbVersion:
    """A new draft version = the base version's documents + the uploaded files. A file whose
    name and content are already in the base is not processed again; a file with an existing
    name but new content replaces that document. The ingest job does the processing."""
    _lock_idle(session, kb.id)
    base = base_version(session, kb.id)
    existing: dict[str, str] = {}
    if base:
        with kb_scope(base.id) as s:
            existing = dict(s.execute(select(Document.file_name, Document.file_hash)).all())
    latest = {f.file_name: f for f in files}  # the same name twice in one upload: last wins
    pending = [f for f in latest.values() if existing.get(f.file_name) != f.file_hash]
    if not pending:
        raise NothingChanged("all uploaded files are already in the course, unchanged")
    version = create_draft_version(session, kb.id)
    version.base_version_id = base.id if base else None
    version.ingest_status = IngestStatus.queued
    version.origin = origin
    version.pending_files = [
        {"file_name": f.file_name, "file_hash": f.file_hash, "path": str(f.path), "source": origin}
        for f in pending
    ]
    session.flush()
    return version


def create_removal_version(
    session: Session,
    kb: KnowledgeBase,
    remove: set[str],
    *,
    from_version: KbVersion | None = None,
    origin: str = "upload",
    auto_activate: bool = False,
) -> KbVersion:
    """A new version = `from_version` (default: the base version) without the named files.
    Nothing is re-processed: the job copies the other documents. `auto_activate` publishes it
    once processed (only the LMS sync uses it: removing material, never adding any)."""
    _lock_idle(session, kb.id)
    base = from_version or base_version(session, kb.id)
    if base is None:
        raise NothingChanged("the course has no material yet")
    with kb_scope(base.id) as s:
        names = set(s.scalars(select(Document.file_name)).all())
    gone = remove & names
    if not gone:
        raise NothingChanged("none of these files is in the course")
    if not names - gone:
        raise NothingChanged("a version needs at least one file; upload the new material first")
    version = create_draft_version(session, kb.id)
    version.base_version_id = base.id
    version.ingest_status = IngestStatus.queued
    version.origin = origin
    version.removed_files = sorted(gone)
    version.auto_activate = auto_activate
    session.flush()
    return version
