"""Teacher sign-in with Microsoft/Google (see core/sso.py for the flow).

    GET /api/sso/providers            the sign-ins this server offers (for the start page)
    GET /api/sso/{provider}/login     start; ?next=<page> (a course page or an invite) optional
    GET /api/sso/{provider}/callback  the provider sends the browser back here

Like an LTI launch, the session token reaches the pages in the URL fragment (#/lti?token=…).
"""

import html
import re
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.lti import _to_pages
from core import sso
from core.db import app_engine
from core.sessions import issue

router = APIRouter(prefix="/api/sso", tags=["sso"])

# where a sign-in may continue to: the course list, a course, an invite (never another site)
NEXT = re.compile(r"^(teacher|teacher/[0-9a-f-]{36}|invite/[A-Za-z0-9_-]{20,80})$")


class ProviderOut(BaseModel):
    id: str
    label: str


@router.get("/providers")
def list_providers() -> list[ProviderOut]:
    return [ProviderOut(id=p.id, label=p.label) for p in sso.providers().values()]


def _error_page(message: str) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='hr'><meta charset='utf-8'><title>CMP</title>"
        "<body style='font-family:system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem'>"
        "<h1>Clone My Professor</h1><p>Prijava nije uspjela.</p>"
        f"<p><small>{html.escape(message)}</small></p><p><a href='/'>Natrag</a></p></body></html>"
    )
    return HTMLResponse(body, status_code=400)


@router.get("/{provider_id}/login")
def login(provider_id: str, next: Annotated[str, Query(max_length=100)] = "teacher"):
    try:
        p = sso.provider(provider_id)
        with Session(app_engine()) as s, s.begin():
            url = sso.begin(s, p, next if NEXT.match(next) else "teacher")
    except sso.SsoError as e:
        return _error_page(str(e))
    return RedirectResponse(url, 302)


@router.get("/{provider_id}/callback")
def callback(
    provider_id: str,
    code: Annotated[str | None, Query(max_length=4000)] = None,
    state: Annotated[str | None, Query(max_length=100)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
):
    if error or not code or not state:
        return _error_page("prijava je prekinuta ili odbijena" + (f" ({error})" if error else ""))
    try:
        with Session(app_engine()) as s:
            try:
                result, next_ = sso.complete(s, provider_id, code, state)
            finally:
                s.commit()  # the state is used up even when the sign-in is refused
            teacher = sso.teacher_for(s, result)
            s.commit()
            token = issue(result.user_ref, "teacher", teacher_id=teacher.id)
    except sso.SsoError as e:
        return _error_page(str(e))
    return _to_pages(next_, token)
