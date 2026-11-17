"""In-app notifications: what a teacher or a student should look at.

Teachers of a course are notified when a draft is ready (or failed), when the LMS sync removed
files, when a question is forwarded to them, and when a persona check ends. A student is
notified when a teacher answers their forwarded question. A failure to notify never breaks the
work it reports on.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.db import app_engine
from core.models import CourseTeacher, Notification

log = logging.getLogger(__name__)

# kind -> what it means (the pages word them for the reader)
KINDS = {
    "draft_ready": "a new version is processed and waits for review",
    "draft_failed": "processing a new version failed",
    "lms_removed": "files gone from the LMS were removed from the published version",
    "question": "a student's question was forwarded",
    "persona_active": "new assistant settings passed the check and are active",
    "persona_rejected": "new assistant settings did not pass the check",
    "answered": "the teacher answered the student's question",
}


def notify_teachers(session: Session, kb_id: uuid.UUID, kind: str, data: dict) -> int:
    teachers = session.scalars(
        select(CourseTeacher.teacher_id).where(CourseTeacher.kb_id == kb_id)
    ).all()
    for t in teachers:
        session.add(Notification(kb_id=kb_id, teacher_id=t, kind=kind, data=data))
    return len(teachers)


def notify_student(
    session: Session, kb_id: uuid.UUID, student_ref: str, kind: str, data: dict
) -> None:
    session.add(Notification(kb_id=kb_id, student_ref=student_ref, kind=kind, data=data))


def notify_teachers_safely(kb_id: uuid.UUID, kind: str, data: dict) -> None:
    """In its own transaction; logged, never raised."""
    try:
        with Session(app_engine()) as s, s.begin():
            notify_teachers(s, kb_id, kind, data)
    except Exception:
        log.exception("could not notify the teachers of %s (%s)", kb_id, kind)


def for_teacher(session: Session, teacher_id: uuid.UUID, unread_only: bool = False, limit=50):
    q = select(Notification).where(Notification.teacher_id == teacher_id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(session.scalars(q.order_by(Notification.created_at.desc()).limit(limit)))


def for_student(session: Session, kb_id: uuid.UUID, student_ref: str, unread_only: bool = True):
    q = select(Notification).where(
        Notification.kb_id == kb_id, Notification.student_ref == student_ref
    )
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(session.scalars(q.order_by(Notification.created_at.desc())))


def mark_read(
    session: Session, ids: list[uuid.UUID], *, teacher_id=None, kb_id=None, student_ref=None
):
    """Mark the recipient's own notifications read (others' ids are ignored)."""
    q = update(Notification).where(Notification.id.in_(ids), Notification.read_at.is_(None))
    if teacher_id is not None:
        q = q.where(Notification.teacher_id == teacher_id)
    else:
        q = q.where(Notification.kb_id == kb_id, Notification.student_ref == student_ref)
    return session.execute(q.values(read_at=datetime.now(UTC))).rowcount
