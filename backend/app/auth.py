"""Who is calling.

- `Authorization: Bearer <session token>` (core/sessions.py), issued after an LTI launch: the
  real sign-in, in every mode.
- Dev mode only (`AUTH_MODE=dev`): the X-CMP-Dev-Teacher / X-CMP-Dev-Student headers name the
  user (no password). Any other mode ignores them, so a production server is closed to them.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine
from core.models import Teacher
from core.sessions import SessionError, verify
from core.settings import get_settings

DEV_HEADER = "X-CMP-Dev-Teacher"
STUDENT_DEV_HEADER = "X-CMP-Dev-Student"


def _bearer(authorization: str | None) -> dict | None:
    """The verified session claims, None without a bearer token; 401 for a bad one."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "expected a bearer token")
    try:
        return verify(token.strip())
    except SessionError as e:
        raise HTTPException(401, "the session has expired or is not valid; open CMP again") from e


def _dev() -> bool:
    return get_settings().auth_mode == "dev"


def current_teacher(
    authorization: Annotated[str | None, Header()] = None,
    x_cmp_dev_teacher: Annotated[str | None, Header()] = None,
) -> Teacher:
    claims = _bearer(authorization)
    if claims is not None:
        if claims.get("role") != "teacher" or "teacher_id" not in claims:
            raise HTTPException(403, "this session is not a teacher's")
        with Session(app_engine(), expire_on_commit=False) as s:
            teacher = s.get(Teacher, uuid.UUID(claims["teacher_id"]))
        if teacher is None:
            raise HTTPException(401, "unknown teacher")
        return teacher
    if not _dev():
        raise HTTPException(401, "sign in through your learning platform")
    name = (x_cmp_dev_teacher or "").strip()
    if not name:
        raise HTTPException(401, f"dev mode: send the {DEV_HEADER} header")
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        teacher = s.scalars(select(Teacher).where(Teacher.name == name[:200])).first()
        if teacher is None:
            teacher = Teacher(name=name[:200])
            s.add(teacher)
    return teacher


TeacherDep = Annotated[Teacher, Depends(current_teacher)]


@dataclass(frozen=True)
class Student:
    ref: str  # opaque (rule 9): never a name or an LMS id
    kbs: frozenset[uuid.UUID] | None  # courses this session may use; None = any (dev only)

    def require(self, kb_id: uuid.UUID) -> None:
        if self.kbs is not None and kb_id not in self.kbs:
            raise HTTPException(403, "open this course from your learning platform")


def current_student(
    authorization: Annotated[str | None, Header()] = None,
    x_cmp_dev_student: Annotated[str | None, Header()] = None,
) -> Student:
    claims = _bearer(authorization)
    if claims is not None:
        if claims.get("role") != "student":
            raise HTTPException(403, "this session is not a student's")
        return Student(claims["sub"], frozenset(uuid.UUID(k) for k in claims.get("kbs", [])))
    if not _dev():
        raise HTTPException(401, "sign in through your learning platform")
    ref = (x_cmp_dev_student or "").strip()
    if not ref:
        raise HTTPException(401, f"dev mode: send the {STUDENT_DEV_HEADER} header")
    return Student(f"dev:{ref[:190]}", None)


StudentDep = Annotated[Student, Depends(current_student)]
