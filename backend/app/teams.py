"""The Teams tab's sign-in (see core/teams.py).

    POST /api/teams/session   {"token": <Teams SSO token>, "team_id": <group id>, "team_name": …}
        -> {"token": <CMP session>, "next": "chat/<kb>" | "teacher/<kb>" | "link" | "not-linked"}

`next` means the same as after an LTI launch, so the pages reuse that flow (#/lti): a teacher of
an unlinked team links it to a CMP course ("link"), students of an unlinked team wait.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core import teams
from core.access import add_teacher
from core.db import app_engine
from core.lti import linked_course
from core.sessions import issue

router = APIRouter(prefix="/api/teams", tags=["teams"])


class SessionIn(BaseModel):
    token: str = Field(min_length=20, max_length=20000)
    team_id: str = Field(min_length=1, max_length=100)
    team_name: str | None = Field(None, max_length=300)


class SessionOut(BaseModel):
    token: str | None
    next: str


@router.post("/session")
def session(body: SessionIn) -> SessionOut:
    if not teams.is_team_id(body.team_id):
        raise HTTPException(422, "open CMP from a team's tab")
    try:
        user = teams.verify_token(body.token)
        with Session(app_engine()) as s:
            platform = teams.platform_for(s, user.tenant_id)
            role = teams.membership(platform, body.team_id, user.object_id)
            if role is None:
                raise teams.TeamsAuthError("you are not a member of this team")
            kb_id = linked_course(s, platform.id, body.team_id)
            if role == "owner":
                teacher = teams.teacher_for(s, user)
                if kb_id:
                    add_teacher(s, kb_id, teacher.id, added_via="teams")
                s.commit()
                if kb_id:
                    token = issue(user.user_ref, "teacher", teacher_id=teacher.id)
                    return SessionOut(token=token, next=f"teacher/{kb_id}")
                pending = {
                    "platform_id": str(platform.id),
                    "context_id": body.team_id,
                    "context_title": body.team_name,
                }
                token = issue(user.user_ref, "teacher", teacher_id=teacher.id, link=pending)
                return SessionOut(token=token, next="link")
    except teams.TeamsAuthError as e:
        raise HTTPException(403, str(e)) from e
    if kb_id is None:
        return SessionOut(token=None, next="not-linked")
    token = issue(f"teams:{user.user_ref}", "student", kbs=[kb_id])
    return SessionOut(token=token, next=f"chat/{kb_id}")
