"""Student chat API (build step 3).

    GET  /api/chat/{kb_id}                      course name, whether it is live, the AI notice
    POST /api/chat/{kb_id}/messages             ask; the reply streams as server-sent events
    GET  /api/chat/{kb_id}/messages             the student's own history
    POST /api/chat/messages/{id}/feedback       👍/👎 on one of the student's own answers

The stream sends `notice` (rule 6: talking to an AI), then `stage` events while the clone works
(scope, search, answer, check), then one `answer` event. Answer tokens are **not** streamed:
the answer check (rule 1) must pass before the student sees anything, so the whole answer is
sent once it has. Keep-alive comments every 15 s hold the connection open on slow hardware.
"""

import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import StudentDep
from core.chat import (
    AI_NOTICE,
    MAX_QUESTION_CHARS,
    CourseNotLive,
    ask,
    course_for_students,
    history,
    set_feedback,
)
from core.db import app_engine
from core.models import ChatMessage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])
KEEP_ALIVE_SECONDS = 15
UNAVAILABLE = "Asistent trenutačno nije dostupan. Pokušajte ponovno za nekoliko minuta."


class CourseInfo(BaseModel):
    kb_id: uuid.UUID
    subject_name: str
    live: bool  # False until the teacher activates a version
    ai_notice: str = AI_NOTICE


class QuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


class Citation(BaseModel):
    n: int
    file_name: str
    pages: list[int]


class MessageOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str  # [n] markers refer to `citations`
    citations: list[Citation]
    forwarded: bool  # True: the question went to the teacher instead of being answered
    feedback: int | None
    created_at: datetime


class FeedbackIn(BaseModel):
    helpful: bool


def _out(m: ChatMessage) -> MessageOut:
    return MessageOut(
        id=m.id,
        question=m.question,
        answer=m.answer,
        citations=[Citation(**c) for c in m.citations],
        forwarded=m.forwarded is not None,
        feedback=m.feedback,
        created_at=m.created_at,
    )


def _live_course(kb_id: uuid.UUID) -> CourseInfo:
    with Session(app_engine()) as s:
        course = course_for_students(s, kb_id)
    if course is None:
        raise HTTPException(404, "course not found")
    return CourseInfo(
        kb_id=course.kb_id, subject_name=course.subject_name, live=course.version_id is not None
    )


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/{kb_id}")
def course_info(kb_id: uuid.UUID, student: StudentDep) -> CourseInfo:
    return _live_course(kb_id)


@router.get("/{kb_id}/messages")
def my_messages(kb_id: uuid.UUID, student: StudentDep) -> list[MessageOut]:
    _live_course(kb_id)
    with Session(app_engine()) as s:
        return [_out(m) for m in history(s, kb_id, student)]


@router.post("/{kb_id}/messages")
def post_question(kb_id: uuid.UUID, body: QuestionIn, student: StudentDep) -> StreamingResponse:
    info = _live_course(kb_id)
    if not info.live:
        raise HTTPException(409, "the teacher has not published this course yet")
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "empty question")

    events: queue.Queue = queue.Queue()

    def work() -> None:
        # Runs to the end even if the student closes the page, so a forwarded question
        # still reaches the teacher's queue and the exchange is stored.
        try:
            message = ask(kb_id, student, question, on_stage=lambda st: events.put(("stage", st)))
            events.put(("answer", message))
        except CourseNotLive:
            events.put(("error", "the teacher has not published this course yet"))
        except Exception:
            log.exception("chat answer failed (course %s)", kb_id)
            events.put(("error", UNAVAILABLE))

    threading.Thread(target=work, daemon=True).start()

    def stream() -> Iterator[str]:
        yield _sse("notice", {"ai_notice": AI_NOTICE})
        while True:
            try:
                kind, value = events.get(timeout=KEEP_ALIVE_SECONDS)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if kind == "stage":
                yield _sse("stage", {"stage": value})
            elif kind == "answer":
                yield _sse("answer", _out(value).model_dump(mode="json"))
                return
            else:
                yield _sse("error", {"detail": value})
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: uuid.UUID, body: FeedbackIn, student: StudentDep) -> MessageOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        message = set_feedback(s, message_id, student, body.helpful)
        if message is None:
            raise HTTPException(404, "message not found")
    return _out(message)
