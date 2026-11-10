"""Knowledge bases and their versions. Every ingest creates a new *draft* version (rule 5);
activation (draft -> active) is a separate, teacher-approved step in build step 3."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import KbVersion, KbVersionStatus, KnowledgeBase, Teacher

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
