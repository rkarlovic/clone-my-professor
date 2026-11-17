"""Student chat: answer from the course's *active* version only, store the exchange, queue
forwarded questions for the teacher (rule 2), collect 👍/👎.

The clone answers one question at a time (no conversation memory yet): the answer pipeline
and its checks were built and measured on single questions.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.answer import Answer, answer_question
from core.approval import active_version
from core.db import app_engine
from core.models import ChatMessage, KnowledgeBase
from core.persona import active_persona, persona_text
from core.queue import forward_to_teacher

# Rule 6 (EU AI Act transparency): every chat says the student is talking to an AI.
AI_NOTICE = (
    "Razgovarate s AI asistentom, ne s nastavnikom. Odgovara samo iz materijala koje je "
    "nastavnik odobrio i navodi izvore; na što ne zna odgovor, prosljeđuje nastavniku."
)
MAX_QUESTION_CHARS = 2000


class CourseNotLive(LookupError):
    """The course has no active version yet: students cannot ask anything."""


@dataclass
class Course:
    kb_id: uuid.UUID
    subject_name: str
    version_id: uuid.UUID | None  # the active version, None while nothing is live


def course_for_students(session: Session, kb_id: uuid.UUID) -> Course | None:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        return None
    active = active_version(session, kb_id)
    return Course(kb.id, kb.subject_name, active.id if active else None)


def citations(answer: Answer) -> list[dict]:
    return [{"n": s.n, "file_name": s.file_name, "pages": s.pages} for s in answer.cited]


def ask(
    kb_id: uuid.UUID,
    student_ref: str,
    question: str,
    *,
    on_stage: Callable[[str], None] | None = None,
    answer_fn: Callable[..., Answer] | None = None,
) -> ChatMessage:
    """Answer `question` from the active version and store the exchange; a forwarded question
    goes to the teacher's queue in the same transaction. The version is fixed when the question
    arrives, so an activation during a slow answer cannot mix two versions."""
    with Session(app_engine()) as s:
        course = course_for_students(s, kb_id)
        persona = active_persona(s, kb_id)
        text = persona_text(persona) if persona else None
        persona_version = persona.version if persona else None
    if course is None or course.version_id is None:
        raise CourseNotLive(str(kb_id))
    answer = (answer_fn or answer_question)(
        course.version_id,
        question,
        on_stage=on_stage,
        persona_text=text,
        persona_version=persona_version,
    )
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        queued = None
        if answer.forwarded:
            queued = forward_to_teacher(
                s, course.version_id, question, answer.forwarded, student_ref
            )
        message = ChatMessage(
            kb_id=kb_id,
            kb_version_id=course.version_id,
            student_ref=student_ref,
            question=question,
            answer=answer.text,
            forwarded=answer.forwarded,
            teacher_question_id=queued.id if queued else None,
            citations=citations(answer),
            trace=answer.trace | {"scope": answer.scope, "check": answer.check},
            seconds=answer.seconds,
            created_at=datetime.now(UTC),
        )
        s.add(message)
    return message


def history(
    session: Session, kb_id: uuid.UUID, student_ref: str, limit: int = 50
) -> list[ChatMessage]:
    """The student's own exchanges in this course, oldest first."""
    rows = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.kb_id == kb_id, ChatMessage.student_ref == student_ref)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def set_feedback(
    session: Session, message_id: uuid.UUID, student_ref: str, helpful: bool
) -> ChatMessage | None:
    """👍/👎 on the student's own message; None if it is not theirs (or does not exist)."""
    message = session.get(ChatMessage, message_id)
    if message is None or message.student_ref != student_ref:
        return None
    message.feedback = 1 if helpful else -1
    message.feedback_at = datetime.now(UTC)
    return message
