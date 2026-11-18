"""A course's teachers (see core/team.py for the rules).

GET    /api/courses/{kb_id}/teachers                 the list (names, roles, how added)
POST   /api/courses/{kb_id}/invites                  a single-use invite link (owners)
DELETE /api/courses/{kb_id}/teachers/{teacher_id}    remove someone (owners) or leave
POST   /api/courses/{kb_id}/teachers/{teacher_id}/owner   make an owner (owners)
GET    /api/invites/{code}                           which course an invite is for
POST   /api/invites/{code}/accept                    join the course (any signed-in teacher)
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import TeacherDep
from app.courses import _own_course
from core import team
from core.db import app_engine
from core.models import KnowledgeBase
from core.settings import get_settings

router = APIRouter(tags=["team"])


class MemberOut(BaseModel):
    teacher_id: uuid.UUID
    name: str
    role: str  # owner | teacher
    added_via: str  # owner | lti | invite
    added_at: datetime
    me: bool


class InviteOut(BaseModel):
    url: str  # shown once; only its hash is stored
    expires_at: datetime


class InviteInfo(BaseModel):
    kb_id: uuid.UUID
    subject_name: str


def _members(s: Session, kb_id: uuid.UUID, me: uuid.UUID) -> list[MemberOut]:
    return [
        MemberOut(**m.__dict__, me=m.teacher_id == me)  # dataclass fields + me
        for m in team.members(s, kb_id)
    ]


@router.get("/api/courses/{kb_id}/teachers")
def list_teachers(kb_id: uuid.UUID, teacher: TeacherDep) -> list[MemberOut]:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        return _members(s, kb_id, teacher.id)


@router.post("/api/courses/{kb_id}/invites", status_code=status.HTTP_201_CREATED)
def invite(kb_id: uuid.UUID, teacher: TeacherDep) -> InviteOut:
    try:
        with Session(app_engine()) as s, s.begin():
            _own_course(s, teacher.id, kb_id)
            code, expires = team.create_invite(s, kb_id, teacher.id)
    except team.TeamError as e:
        raise HTTPException(403, str(e)) from e
    return InviteOut(
        url=f"{get_settings().public_url.rstrip('/')}/#/invite/{code}", expires_at=expires
    )


@router.delete("/api/courses/{kb_id}/teachers/{teacher_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_teacher(kb_id: uuid.UUID, teacher_id: uuid.UUID, teacher: TeacherDep) -> Response:
    try:
        with Session(app_engine()) as s, s.begin():
            _own_course(s, teacher.id, kb_id)
            team.remove(s, kb_id, teacher_id, teacher.id)
    except team.TeamError as e:
        raise HTTPException(409, str(e)) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/courses/{kb_id}/teachers/{teacher_id}/owner")
def make_owner(kb_id: uuid.UUID, teacher_id: uuid.UUID, teacher: TeacherDep) -> list[MemberOut]:
    try:
        with Session(app_engine()) as s, s.begin():
            _own_course(s, teacher.id, kb_id)
            team.make_owner(s, kb_id, teacher_id, teacher.id)
            return _members(s, kb_id, teacher.id)
    except team.TeamError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/api/invites/{code}")
def invite_info(code: str, teacher: TeacherDep) -> InviteInfo:
    try:
        with Session(app_engine()) as s:
            kb = team.invite_course(s, code)
            return InviteInfo(kb_id=kb.id, subject_name=kb.subject_name)
    except team.TeamError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/api/invites/{code}/accept")
def accept(code: str, teacher: TeacherDep) -> InviteInfo:
    try:
        with Session(app_engine()) as s, s.begin():
            kb_id = team.accept_invite(s, code, teacher.id)
            kb = s.get(KnowledgeBase, kb_id)
            return InviteInfo(kb_id=kb.id, subject_name=kb.subject_name)
    except team.TeamError as e:
        raise HTTPException(404, str(e)) from e
