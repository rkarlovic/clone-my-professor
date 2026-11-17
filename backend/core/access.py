"""Who may manage a course: every teacher on its `course_teacher` list (several per course).

The creator is the owner; instructors of a linked LMS course join automatically on their first
launch (the LMS vouches for them). All teachers of a course have the same rights for now; the
role is kept for later (e.g. only the owner removing a co-teacher).
"""

import uuid

from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from core.models import CourseTeacher, KnowledgeBase


def teaches(session: Session, teacher_id: uuid.UUID, kb_id: uuid.UUID) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(CourseTeacher.kb_id == kb_id, CourseTeacher.teacher_id == teacher_id)
            )
        )
    )


def courses_of(teacher_id: uuid.UUID) -> Select:
    """The teacher's courses, by name."""
    mine = select(CourseTeacher.kb_id).where(CourseTeacher.teacher_id == teacher_id)
    return (
        select(KnowledgeBase).where(KnowledgeBase.id.in_(mine)).order_by(KnowledgeBase.subject_name)
    )


def add_teacher(
    session: Session,
    kb_id: uuid.UUID,
    teacher_id: uuid.UUID,
    *,
    role: str = "teacher",
    added_via: str = "owner",
) -> bool:
    """Put a teacher on the course's list; False if already there."""
    if teaches(session, teacher_id, kb_id):
        return False
    session.add(CourseTeacher(kb_id=kb_id, teacher_id=teacher_id, role=role, added_via=added_via))
    session.flush()
    return True


def create_course(session: Session, teacher_id: uuid.UUID, subject_name: str) -> KnowledgeBase:
    kb = KnowledgeBase(teacher_id=teacher_id, subject_name=subject_name.strip())
    session.add(kb)
    session.flush()
    add_teacher(session, kb.id, teacher_id, role="owner")
    return kb


def teacher_count(session: Session, kb_id: uuid.UUID) -> int:
    q = select(CourseTeacher.teacher_id).where(CourseTeacher.kb_id == kb_id)
    return len(session.scalars(q).all())
