"""LTI 1.3 endpoints (build step 4). See core/lti.py for the flow.

    GET|POST /api/lti/login     OIDC login initiation (from the LMS)
    POST     /api/lti/launch    the signed launch (from the LMS, via the browser)
    GET      /api/lti/jwks      the tool's public key set
    GET      /api/lti/config    the URLs an LMS administrator enters
    GET      /api/lti/link      a teacher's pending LMS course and their CMP courses
    POST     /api/lti/link      link that LMS course to a CMP course (existing or new)

After a launch the browser goes to the web pages with a session token in the URL *fragment*
(#/lti?token=…): fragments are not sent to servers, so the token stays out of logs and
Referer headers. The pages keep it for the tab only.
"""

import html
import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import _bearer
from core.access import add_teacher, courses_of, create_course
from core.db import app_engine
from core.lti import (
    LtiError,
    begin_login,
    complete_launch,
    link_course,
    linked_course,
    teacher_for,
    tool_jwks,
    tool_urls,
)
from core.sessions import issue
from core.settings import get_settings

router = APIRouter(prefix="/api/lti", tags=["lti"])


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='hr'><meta charset='utf-8'><title>CMP</title>"
        "<body style='font-family:system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem'>"
        "<h1>Clone My Professor</h1><p>CMP se nije mogao otvoriti iz sustava za učenje.</p>"
        f"<p><small>{html.escape(message)}</small></p></body></html>"
    )
    return HTMLResponse(body, status_code=status)


def _to_pages(next_: str, token: str | None = None) -> RedirectResponse:
    query = urlencode({"next": next_} | ({"token": token} if token else {}))
    return RedirectResponse(f"{get_settings().public_url.rstrip('/')}/#/lti?{query}", 303)


@router.api_route("/login", methods=["GET", "POST"])
async def login(request: Request):
    params = dict(request.query_params)
    if request.method == "POST":
        params |= {k: str(v) for k, v in (await request.form()).items()}
    try:
        with Session(app_engine()) as s, s.begin():
            url = begin_login(s, params)
    except LtiError as e:
        return _error_page(str(e))
    return RedirectResponse(url, 302)


@router.post("/launch")
def launch(id_token: Annotated[str, Form()], state: Annotated[str, Form()]):
    try:
        with Session(app_engine()) as s:
            try:
                result = complete_launch(s, id_token, state)
            finally:
                s.commit()  # the state is used up even when the launch is refused
            if result.is_teacher:
                teacher = teacher_for(s, result)
                kb_id = linked_course(s, result.platform_id, result.context_id)
                if kb_id:
                    # an instructor of a linked LMS course joins its CMP course (the LMS vouches)
                    add_teacher(s, kb_id, teacher.id, added_via="lti")
                s.commit()
                if kb_id:
                    token = issue(result.user_ref, "teacher", teacher_id=teacher.id)
                    return _to_pages(f"teacher/{kb_id}", token)
                pending = {
                    "platform_id": str(result.platform_id),
                    "context_id": result.context_id,
                    "context_title": result.context_title,
                }
                token = issue(result.user_ref, "teacher", teacher_id=teacher.id, link=pending)
                return _to_pages("link", token)
            kb_id = linked_course(s, result.platform_id, result.context_id)
    except LtiError as e:
        return _error_page(str(e))
    if kb_id is None:
        return _to_pages("not-linked")
    # Students: an opaque reference only (rule 9), allowed in the linked course.
    return _to_pages(f"chat/{kb_id}", issue(f"lti:{result.user_ref}", "student", kbs=[kb_id]))


@router.get("/jwks")
def jwks() -> dict:
    return tool_jwks()


@router.get("/config")
def config() -> dict:
    return tool_urls()


def _link_claims(authorization: Annotated[str | None, Header()] = None) -> dict:
    claims = _bearer(authorization)
    if not claims or claims.get("role") != "teacher" or "link" not in claims:
        raise HTTPException(403, "open CMP from the LMS course as its teacher")
    return claims


LinkClaims = Annotated[dict, Depends(_link_claims)]


class CourseChoice(BaseModel):
    id: uuid.UUID
    subject_name: str


class LinkInfo(BaseModel):
    context_title: str | None
    courses: list[CourseChoice]


class LinkIn(BaseModel):
    kb_id: uuid.UUID | None = None
    new_course_name: str | None = Field(None, max_length=300)


class LinkOut(BaseModel):
    kb_id: uuid.UUID
    token: str  # a teacher session without the pending link


@router.get("/link")
def link_info(claims: LinkClaims) -> LinkInfo:
    teacher_id = uuid.UUID(claims["teacher_id"])
    with Session(app_engine()) as s:
        rows = s.scalars(courses_of(teacher_id)).all()
        courses = [CourseChoice(id=k.id, subject_name=k.subject_name) for k in rows]
    return LinkInfo(context_title=claims["link"].get("context_title"), courses=courses)


@router.post("/link")
def link(body: LinkIn, claims: LinkClaims) -> LinkOut:
    teacher_id = uuid.UUID(claims["teacher_id"])
    pending = claims["link"]
    if (body.kb_id is None) == (not (body.new_course_name or "").strip()):
        raise HTTPException(422, "choose a course or give a name for a new one")
    try:
        with Session(app_engine()) as s, s.begin():
            kb_id = body.kb_id
            if kb_id is None:
                kb_id = create_course(s, teacher_id, body.new_course_name).id
            link_course(
                s,
                teacher_id,
                uuid.UUID(pending["platform_id"]),
                pending["context_id"],
                pending.get("context_title"),
                kb_id,
            )
    except LtiError as e:
        raise HTTPException(409, str(e)) from e
    return LinkOut(kb_id=kb_id, token=issue(claims["sub"], "teacher", teacher_id=teacher_id))
