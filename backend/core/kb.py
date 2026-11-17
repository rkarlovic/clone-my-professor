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
    kb = KnowledgeBase(teacher_id=teacher.id, subject_name=subject)
    session.add(kb)
    session.flush()
    return kb


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
    """What a new upload builds on: the active version, else the newest processed one."""
    versions = session.scalars(
        select(KbVersion).where(KbVersion.kb_id == kb_id).order_by(KbVersion.version.desc())
    ).all()
    active = [v for v in versions if v.status == KbVersionStatus.active]
    ready = [v for v in versions if v.ingest_status == IngestStatus.ready]
    return (active or ready or [None])[0]


def create_upload_version(
    session: Session, kb: KnowledgeBase, files: Sequence[StoredFile]
) -> KbVersion:
    """A new draft version = the base version's documents + the uploaded files. A file whose
    name and content are already in the base is not processed again; a file with an existing
    name but new content replaces that document. The ingest job does the processing."""
    # One upload per course at a time: two concurrent requests would both pass the check below.
    session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == kb.id).with_for_update())
    busy = session.scalar(
        select(KbVersion.version).where(
            KbVersion.kb_id == kb.id,
            KbVersion.ingest_status.in_([IngestStatus.queued, IngestStatus.processing]),
        )
    )
    if busy:
        raise StillProcessing(f"version {busy} of this course is still being processed")
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
    version.pending_files = [
        {"file_name": f.file_name, "file_hash": f.file_hash, "path": str(f.path)} for f in pending
    ]
    session.flush()
    return version
