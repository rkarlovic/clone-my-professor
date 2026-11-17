"""Who is calling. Build step 3 has no sign-in yet: in `auth_mode = dev` the teacher is named by
the X-CMP-Dev-Teacher header (created on first use). Any other mode refuses every request
until SSO (Microsoft/Google) is added, so a misconfigured server is closed, not open."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine
from core.models import Teacher
from core.settings import get_settings

DEV_HEADER = "X-CMP-Dev-Teacher"


def current_teacher(
    x_cmp_dev_teacher: Annotated[str | None, Header()] = None,
) -> Teacher:
    if get_settings().auth_mode != "dev":
        raise HTTPException(401, "sign-in is not configured")
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
