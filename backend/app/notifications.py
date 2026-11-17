"""In-app notifications.

GET  /api/notifications[?unread=true]           the teacher's, newest first
POST /api/notifications/read                    {"ids": [...]} or {"all": true}
GET  /api/chat/{kb_id}/notifications            a student's unread ones in this course
POST /api/chat/{kb_id}/notifications/read       {"ids": [...]} or {"all": true}
"""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import StudentDep, TeacherDep
from core.db import app_engine
from core.models import KnowledgeBase, Notification
from core.notify import for_student, for_teacher, mark_read

router = APIRouter(tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    course: str | None = None
    kind: str
    data: dict
    created_at: datetime
    read: bool


class ReadIn(BaseModel):
    ids: list[uuid.UUID] = []
    all: bool = False


def _out(n: Notification, course: str | None = None) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        kb_id=n.kb_id,
        course=course,
        kind=n.kind,
        data=n.data,
        created_at=n.created_at,
        read=n.read_at is not None,
    )


@router.get("/api/notifications")
def teacher_notifications(teacher: TeacherDep, unread: bool = False) -> list[NotificationOut]:
    with Session(app_engine()) as s:
        rows = for_teacher(s, teacher.id, unread_only=unread)
        names = dict(
            s.execute(
                select(KnowledgeBase.id, KnowledgeBase.subject_name).where(
                    KnowledgeBase.id.in_({n.kb_id for n in rows})
                )
            ).all()
        )
        return [_out(n, names.get(n.kb_id)) for n in rows]


@router.post("/api/notifications/read")
def teacher_read(body: ReadIn, teacher: TeacherDep) -> dict:
    with Session(app_engine()) as s, s.begin():
        if body.all:
            done = s.execute(
                update(Notification)
                .where(Notification.teacher_id == teacher.id, Notification.read_at.is_(None))
                .values(read_at=datetime.now().astimezone())
            ).rowcount
        else:
            done = mark_read(s, body.ids, teacher_id=teacher.id)
    return {"read": done}


@router.get("/api/chat/{kb_id}/notifications")
def student_notifications(kb_id: uuid.UUID, student: StudentDep) -> list[NotificationOut]:
    student.require(kb_id)
    with Session(app_engine()) as s:
        return [_out(n) for n in for_student(s, kb_id, student.ref)]


@router.post("/api/chat/{kb_id}/notifications/read")
def student_read(kb_id: uuid.UUID, body: ReadIn, student: StudentDep) -> dict:
    student.require(kb_id)
    with Session(app_engine()) as s, s.begin():
        ids = [n.id for n in for_student(s, kb_id, student.ref)] if body.all else body.ids
        done = mark_read(s, ids, kb_id=kb_id, student_ref=student.ref)
    return {"read": done}
