"""Teacher API: the course's persona (how the clone talks; instruction layer 2).

GET  /api/courses/{kb_id}/persona                     all versions, newest first
PUT  /api/courses/{kb_id}/persona                     save the draft (creates one if none)
POST /api/courses/{kb_id}/persona/{n}/preview         ask a test question with the draft
                                                      (server-sent events; nothing stored)
POST /api/courses/{kb_id}/persona/{n}/activate        queue the activation check
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeacherDep
from app.courses import _own_course
from app.streaming import stream_answer
from core.approval import active_version
from core.chat import MAX_QUESTION_CHARS, citations
from core.db import app_engine
from core.models import PersonaConfig
from core.persona import (
    FREE_TEXT_MAX,
    PersonaError,
    PersonaSettings,
    persona_text,
    save_draft,
    start_activation,
)
from jobs import enqueue_persona_check

router = APIRouter(prefix="/api/courses", tags=["persona"])


class PersonaIn(BaseModel):
    settings: PersonaSettings
    free_text: str = Field("", max_length=FREE_TEXT_MAX)


class PersonaOut(BaseModel):
    version: int
    status: str  # draft | checking | active | rejected | retired
    settings: PersonaSettings
    free_text: str
    prompt_text: str  # exactly what is added to the answer prompt
    created_at: datetime
    activated_at: datetime | None
    check_report: dict | None


class PreviewIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def _out(p: PersonaConfig) -> PersonaOut:
    return PersonaOut(
        version=p.version,
        status=p.status,
        settings=PersonaSettings(**p.settings),
        free_text=p.free_text,
        prompt_text=persona_text(p),
        created_at=p.created_at,
        activated_at=p.activated_at,
        check_report=p.check_report,
    )


def _persona(s: Session, kb_id: uuid.UUID, number: int) -> PersonaConfig:
    p = s.scalars(
        select(PersonaConfig).where(PersonaConfig.kb_id == kb_id, PersonaConfig.version == number)
    ).first()
    if p is None:
        raise HTTPException(404, "persona version not found")
    return p


@router.get("/{kb_id}/persona")
def list_personas(kb_id: uuid.UUID, teacher: TeacherDep) -> list[PersonaOut]:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        rows = s.scalars(
            select(PersonaConfig)
            .where(PersonaConfig.kb_id == kb_id)
            .order_by(PersonaConfig.version.desc())
        ).all()
        return [_out(p) for p in rows]


@router.put("/{kb_id}/persona")
def save(kb_id: uuid.UUID, body: PersonaIn, teacher: TeacherDep) -> PersonaOut:
    """Save the settings as the course's draft persona (students do not get it yet)."""
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        _own_course(s, teacher.id, kb_id)
        try:
            p = save_draft(s, kb_id, teacher.id, body.settings, body.free_text)
        except PersonaError as e:
            raise HTTPException(422, str(e)) from e
    with Session(app_engine()) as s:  # created_at is set by the database
        return _out(s.get(PersonaConfig, p.id))


@router.post("/{kb_id}/persona/{number}/preview")
def preview(
    kb_id: uuid.UUID, number: int, body: PreviewIn, teacher: TeacherDep
) -> StreamingResponse:
    """The clone's answer to a test question with this persona, from the published material.
    Same pipeline and checks as the chat; nothing is stored or forwarded."""
    from core.answer import answer_question

    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        p = _persona(s, kb_id, number)
        version = active_version(s, kb_id)
        if version is None:
            raise HTTPException(409, "publish the course materials first")
        text, version_id = persona_text(p), version.id

    def run(on_stage) -> dict:
        a = answer_question(
            version_id,
            body.question.strip(),
            on_stage=on_stage,
            persona_text=text,
            persona_version=number,
        )
        return {
            "question": body.question.strip(),
            "answer": a.text,
            "citations": citations(a),
            "forwarded": a.forwarded is not None,
            "forward_reason": a.forwarded.value if a.forwarded else None,
        }

    return stream_answer(run)


@router.post("/{kb_id}/persona/{number}/activate", status_code=202)
def activate(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> PersonaOut:
    """Check the draft on a sample of the course's check questions and the generic forward
    questions; it becomes active only if forwarding and answering stay above the thresholds."""
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        _own_course(s, teacher.id, kb_id)
        p = _persona(s, kb_id, number)
        try:
            start_activation(s, p)
        except PersonaError as e:
            raise HTTPException(409, str(e)) from e
    try:
        enqueue_persona_check(p.id, kb_id)
    except Exception as e:
        with Session(app_engine(), expire_on_commit=False) as s, s.begin():
            p = s.get(PersonaConfig, p.id)
            p.status = "rejected"
            p.check_report = {"passed": False, "reasons": [f"could not queue ({type(e).__name__})"]}
    with Session(app_engine()) as s:
        return _out(s.get(PersonaConfig, p.id))
