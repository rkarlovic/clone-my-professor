"""Student chat API (build step 3).

    GET  /api/chat/{kb_id}                      course name, whether it is live, the AI notice
    POST /api/chat/{kb_id}/messages             ask; the reply streams as server-sent events
    GET  /api/chat/{kb_id}/messages             the student's own history
    POST /api/chat/messages/{id}/feedback       👍/👎 on one of the student's own answers

The stream sends `notice` (rule 6: talking to an AI), then `stage` events while the clone works
(followup when a short follow-up is rewritten first, scope, search, answer, check), then one
`answer` event. Answer tokens are **not** streamed:
the answer check (rule 1) must pass before the student sees anything, so the whole answer is
sent once it has. Keep-alive comments every 15 s hold the connection open on slow hardware.
"""

import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import StudentDep
from app.streaming import UserFacingError, stream_answer
from core.chat import (
    AI_NOTICE,
    MAX_QUESTION_CHARS,
    CourseNotLive,
    TooManyQuestions,
    ask,
    check_limit,
    course_for_students,
    history,
    set_feedback,
)
from core.db import app_engine
from core.models import ChatMessage, TeacherQuestion, TeacherQuestionStatus

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
    # The teacher's own answer to a forwarded question, once given (written by a person, not
    # the AI: the UI shows it as the teacher's).
    teacher_answer: str | None = None
    teacher_answered_at: datetime | None = None


class FeedbackIn(BaseModel):
    helpful: bool


def _out(m: ChatMessage, teacher: TeacherQuestion | None = None) -> MessageOut:
    answered = teacher is not None and teacher.status == TeacherQuestionStatus.answered
    return MessageOut(
        id=m.id,
        question=m.question,
        answer=m.answer,
        citations=[Citation(**c) for c in m.citations],
        forwarded=m.forwarded is not None,
        feedback=m.feedback,
        created_at=m.created_at,
        teacher_answer=teacher.answer if answered else None,
        teacher_answered_at=teacher.answered_at if answered else None,
    )


def _live_course(kb_id: uuid.UUID) -> CourseInfo:
    with Session(app_engine()) as s:
        course = course_for_students(s, kb_id)
    if course is None:
        raise HTTPException(404, "course not found")
    return CourseInfo(
        kb_id=course.kb_id, subject_name=course.subject_name, live=course.version_id is not None
    )


@router.get("/{kb_id}")
def course_info(kb_id: uuid.UUID, student: StudentDep) -> CourseInfo:
    student.require(kb_id)
    return _live_course(kb_id)


@router.get("/{kb_id}/messages")
def my_messages(kb_id: uuid.UUID, student: StudentDep) -> list[MessageOut]:
    student.require(kb_id)
    _live_course(kb_id)
    with Session(app_engine()) as s:
        messages = history(s, kb_id, student.ref)
        ids = [m.teacher_question_id for m in messages if m.teacher_question_id]
        answers = {q.id: q for q in s.query(TeacherQuestion).filter(TeacherQuestion.id.in_(ids))}
        return [_out(m, answers.get(m.teacher_question_id)) for m in messages]


# One question at a time per student and course (per API process; the hourly limit in
# core/chat.py holds across processes): a second one while the first is answered is refused.
_in_flight: set[tuple[uuid.UUID, str]] = set()
_in_flight_lock = threading.Lock()


def _limit_message(minutes: int) -> str:
    return (
        "Postavili ste mnogo pitanja u kratko vrijeme. Sljedeće pitanje možete postaviti za "
        f"{minutes} min."
    )


@router.post("/{kb_id}/messages")
def post_question(kb_id: uuid.UUID, body: QuestionIn, student: StudentDep) -> StreamingResponse:
    student.require(kb_id)
    info = _live_course(kb_id)
    if not info.live:
        raise HTTPException(409, "the teacher has not published this course yet")
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "empty question")
    with Session(app_engine()) as s:
        try:
            check_limit(s, kb_id, student.ref)
        except TooManyQuestions as e:
            raise HTTPException(429, _limit_message(e.minutes)) from e
    key = (kb_id, student.ref)
    with _in_flight_lock:
        if key in _in_flight:
            raise HTTPException(429, "Pričekajte odgovor na prethodno pitanje.")
        _in_flight.add(key)

    def run(on_stage) -> dict:
        # Runs to the end even if the student closes the page, so a forwarded question
        # still reaches the teacher's queue and the exchange is stored.
        try:
            message = ask(kb_id, student.ref, question, on_stage=on_stage)
        except CourseNotLive as e:
            raise UserFacingError("the teacher has not published this course yet") from e
        except TooManyQuestions as e:
            raise UserFacingError(_limit_message(e.minutes)) from e
        finally:
            with _in_flight_lock:
                _in_flight.discard(key)
        return _out(message).model_dump(mode="json")

    try:
        return stream_answer(run)
    except Exception:
        with _in_flight_lock:
            _in_flight.discard(key)
        raise


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: uuid.UUID, body: FeedbackIn, student: StudentDep) -> MessageOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        message = set_feedback(s, message_id, student.ref, body.helpful)
        if message is None:
            raise HTTPException(404, "message not found")
    return _out(message)
