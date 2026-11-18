"""A course's teachers: list, invite (no LMS needed), remove, make owner.

Rules: owners invite, remove others and make others owners; any teacher may leave; a course
always keeps at least one owner. An instructor of a linked LMS course who is removed here joins
again on their next launch from that course (the LMS stays the source of truth for its own
instructors; remove them there too).

An invite is a single-use code valid `INVITE_DAYS`; only its sha256 is stored.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.access import add_teacher
from core.models import CourseInvite, CourseTeacher, KnowledgeBase, Teacher

INVITE_DAYS = 7


class TeamError(ValueError):
    """The change is not allowed; the message is safe to show."""


@dataclass(frozen=True)
class Member:
    teacher_id: uuid.UUID
    name: str
    role: str
    added_via: str
    added_at: datetime


def members(session: Session, kb_id: uuid.UUID) -> list[Member]:
    rows = session.execute(
        select(CourseTeacher, Teacher.name)
        .join(Teacher, Teacher.id == CourseTeacher.teacher_id)
        .where(CourseTeacher.kb_id == kb_id)
        .order_by(CourseTeacher.created_at)
    ).all()
    return [Member(ct.teacher_id, name, ct.role, ct.added_via, ct.created_at) for ct, name in rows]


def _row(session: Session, kb_id: uuid.UUID, teacher_id: uuid.UUID) -> CourseTeacher | None:
    return session.get(CourseTeacher, (kb_id, teacher_id), with_for_update=True)


def _is_owner(session: Session, kb_id: uuid.UUID, teacher_id: uuid.UUID) -> bool:
    row = session.get(CourseTeacher, (kb_id, teacher_id))
    return row is not None and row.role == "owner"


def _lock(session: Session, kb_id: uuid.UUID) -> None:
    session.get(KnowledgeBase, kb_id, with_for_update=True)


def _owners(session: Session, kb_id: uuid.UUID) -> int:
    q = select(func.count()).where(CourseTeacher.kb_id == kb_id, CourseTeacher.role == "owner")
    return session.scalar(q) or 0


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_invite(session: Session, kb_id: uuid.UUID, by: uuid.UUID) -> tuple[str, datetime]:
    """A new invite code (shown once) and when it expires."""
    if not _is_owner(session, kb_id, by):
        raise TeamError("only the course's owners can invite teachers")
    code = secrets.token_urlsafe(24)
    expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
    session.add(CourseInvite(kb_id=kb_id, code_hash=_hash(code), created_by=by, expires_at=expires))
    session.flush()
    return code, expires


def invite_course(session: Session, code: str) -> KnowledgeBase:
    """The course an open invite is for (to show its name before accepting)."""
    inv = session.scalars(select(CourseInvite).where(CourseInvite.code_hash == _hash(code))).first()
    if inv is None or inv.used_at is not None or inv.expires_at < datetime.now(UTC):
        raise TeamError("this invite is not valid any more; ask for a new one")
    return session.get(KnowledgeBase, inv.kb_id)


def accept_invite(session: Session, code: str, teacher_id: uuid.UUID) -> uuid.UUID:
    q = select(CourseInvite).where(CourseInvite.code_hash == _hash(code)).with_for_update()
    inv = session.scalars(q).first()
    if inv is None or inv.used_at is not None or inv.expires_at < datetime.now(UTC):
        raise TeamError("this invite is not valid any more; ask for a new one")
    if add_teacher(session, inv.kb_id, teacher_id, added_via="invite"):
        inv.used_at, inv.used_by = datetime.now(UTC), teacher_id
    # already a teacher of the course: the invite stays open for the person it was meant for
    return inv.kb_id


def remove(session: Session, kb_id: uuid.UUID, teacher_id: uuid.UUID, by: uuid.UUID) -> None:
    _lock(session, kb_id)
    if teacher_id != by and not _is_owner(session, kb_id, by):
        raise TeamError("only the course's owners can remove other teachers")
    row = _row(session, kb_id, teacher_id)
    if row is None:
        raise TeamError("this teacher is not on the course")
    if row.role == "owner" and _owners(session, kb_id) <= 1:
        raise TeamError("the course needs another owner first")
    session.delete(row)
    session.flush()


def make_owner(session: Session, kb_id: uuid.UUID, teacher_id: uuid.UUID, by: uuid.UUID) -> None:
    _lock(session, kb_id)
    if not _is_owner(session, kb_id, by):
        raise TeamError("only the course's owners can make other teachers owners")
    row = _row(session, kb_id, teacher_id)
    if row is None:
        raise TeamError("this teacher is not on the course")
    row.role = "owner"
