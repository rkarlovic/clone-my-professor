"""Teacher API: the question queue of a course (rule 2).

    GET  /api/courses/{kb_id}/questions[?status=open|answered|dismissed|all]   (default open)
    POST /api/courses/{kb_id}/questions/{id}/answer     {"answer", "sources": [{file_name, page}]}
    POST /api/courses/{kb_id}/questions/{id}/dismiss

No student identity is returned (rule 9): the teacher needs the question, not who asked.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import TeacherDep
from app.courses import _own_course
from core.db import app_engine
from core.models import TeacherQuestion, TeacherQuestionStatus
from core.queue import QueueError, SourceRef, answer_forwarded, dismiss_forwarded, list_questions

router = APIRouter(prefix="/api/courses", tags=["questions"])

# Why the clone forwarded it, in words a teacher reads in the queue.
REASONS = {
    "admin": "pitanje o ocjenama, rokovima ili obvezama",
    "out_of_scope": "nije iz gradiva kolegija",
    "low_score": "u materijalima nije pronađeno ništa dovoljno blisko",
    "no_source": "model nije našao odgovor u materijalima",
    "failed_check": "provjera odgovora nije prošla",
}


class SourceIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=500)
    page: int | None = None


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)
    sources: list[SourceIn] = []


class QuestionOut(BaseModel):
    id: uuid.UUID
    question: str
    reason: str
    reason_text: str
    status: str
    created_at: datetime
    answer: str | None
    answer_sources: list[SourceIn]
    answered_at: datetime | None


def _out(q: TeacherQuestion) -> QuestionOut:
    return QuestionOut(
        id=q.id,
        question=q.question,
        reason=q.reason.value,
        reason_text=REASONS.get(q.reason.value, q.reason.value),
        status=q.status.value,
        created_at=q.created_at,
        answer=q.answer,
        answer_sources=[SourceIn(**s) for s in q.answer_sources],
        answered_at=q.answered_at,
    )


def _own_question(s: Session, teacher_id: uuid.UUID, kb_id: uuid.UUID, qid: uuid.UUID):
    _own_course(s, teacher_id, kb_id)
    item = s.get(TeacherQuestion, qid)
    if item is None or item.kb_id != kb_id:
        raise HTTPException(404, "question not found")
    return item


@router.get("/{kb_id}/questions")
def queue(
    kb_id: uuid.UUID,
    teacher: TeacherDep,
    status: Literal["open", "answered", "dismissed", "all"] = "open",
) -> list[QuestionOut]:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        wanted = None if status == "all" else TeacherQuestionStatus(status)
        return [_out(q) for q in list_questions(s, kb_id, wanted)]


@router.post("/{kb_id}/questions/{question_id}/answer")
def answer(
    kb_id: uuid.UUID, question_id: uuid.UUID, body: AnswerIn, teacher: TeacherDep
) -> QuestionOut:
    """Answer a forwarded question (or correct an earlier answer). The student sees it under
    their question in the chat history."""
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        item = _own_question(s, teacher.id, kb_id, question_id)
        try:
            answer_forwarded(
                s,
                item,
                teacher.id,
                body.answer,
                [SourceRef(x.file_name, x.page) for x in body.sources],
            )
        except QueueError as e:
            raise HTTPException(422, str(e)) from e
    return _out(item)


@router.post("/{kb_id}/questions/{question_id}/dismiss")
def dismiss(kb_id: uuid.UUID, question_id: uuid.UUID, teacher: TeacherDep) -> QuestionOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        item = _own_question(s, teacher.id, kb_id, question_id)
        try:
            dismiss_forwarded(s, item)
        except QueueError as e:
            raise HTTPException(409, str(e)) from e
    return _out(item)
