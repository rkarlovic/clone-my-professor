"""Student chat: answer from the course's *active* version only, store the exchange, queue
forwarded questions for the teacher (rule 2), collect 👍/👎.

The answer pipeline answers one question at a time (it was built and measured on single
questions). A short follow-up right after an answer is first rewritten to stand on its own
(core/followup.py); the rewritten question is what gets answered and forwarded.

Each student has a limit of questions per course per hour (`CHAT_QUESTIONS_PER_HOUR`, counted
from the stored exchanges, so it holds across API instances) and one question at a time
(app/chat.py): the GPU is the scarce resource, and one student must not hold it for everyone.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import followup
from core.answer import Answer, answer_question
from core.approval import active_version
from core.db import app_engine
from core.models import ChatMessage, ForwardReason, KnowledgeBase
from core.notify import notify_teachers
from core.persona import active_persona, persona_text
from core.queue import forward_to_teacher
from core.settings import get_settings

# Rule 6 (EU AI Act transparency): every chat says the student is talking to an AI.
AI_NOTICE = (
    "Razgovarate s AI asistentom, ne s nastavnikom. Odgovara samo iz materijala koje je "
    "nastavnik odobrio i navodi izvore; na što ne zna odgovor, prosljeđuje nastavniku."
)
MAX_QUESTION_CHARS = 2000


class CourseNotLive(LookupError):
    """The course has no active version yet: students cannot ask anything."""


class TooManyQuestions(RuntimeError):
    """The student reached the hourly limit for this course; `minutes` until the next one."""

    def __init__(self, minutes: int):
        super().__init__(f"limit reached; try again in {minutes} min")
        self.minutes = minutes


def check_limit(session: Session, kb_id: uuid.UUID, student_ref: str) -> None:
    limit = get_settings().chat_questions_per_hour
    if limit <= 0:
        return
    since = datetime.now(UTC) - timedelta(hours=1)
    count, oldest = session.execute(
        select(func.count(), func.min(ChatMessage.created_at)).where(
            ChatMessage.kb_id == kb_id,
            ChatMessage.student_ref == student_ref,
            ChatMessage.created_at > since,
        )
    ).one()
    if count >= limit:
        wait = oldest + timedelta(hours=1) - datetime.now(UTC)
        raise TooManyQuestions(max(1, int(wait.total_seconds() // 60) + 1))


def _recent(session: Session, kb_id: uuid.UUID, student_ref: str):
    """The student's last exchanges here, oldest first, and when the last one was."""
    rows = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.kb_id == kb_id, ChatMessage.student_ref == student_ref)
        .order_by(ChatMessage.created_at.desc())
        .limit(followup.TURNS)
    ).all()
    exchanges = [(m.standalone or m.question, m.answer) for m in reversed(rows)]
    return exchanges, (rows[0].created_at if rows else None)


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
    rewrite_fn: Callable[..., str | None] | None = None,
) -> ChatMessage:
    """Answer `question` from the active version and store the exchange; a forwarded question
    goes to the teacher's queue in the same transaction. The version is fixed when the question
    arrives, so an activation during a slow answer cannot mix two versions."""
    with Session(app_engine()) as s:
        course = course_for_students(s, kb_id)
        persona = active_persona(s, kb_id)
        text = persona_text(persona) if persona else None
        persona_version = persona.version if persona else None
        if course is not None and course.version_id is not None:
            check_limit(s, kb_id, student_ref)
        exchanges, last_at = _recent(s, kb_id, student_ref)
    if course is None or course.version_id is None:
        raise CourseNotLive(str(kb_id))
    standalone = None
    if followup.needs_rewrite(question, last_at):
        if on_stage:
            on_stage("followup")
        standalone = (rewrite_fn or followup.rewrite)(course.subject_name, exchanges, question)
    asked = standalone or question
    answer = (answer_fn or answer_question)(
        course.version_id,
        asked,
        on_stage=on_stage,
        persona_text=text,
        persona_version=persona_version,
    )
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        queued = None
        if answer.forwarded:
            queued = forward_to_teacher(s, course.version_id, asked, answer.forwarded, student_ref)
            notify_teachers(
                s, kb_id, "question", {"question": asked[:200], "reason": answer.forwarded.value}
            )
        message = ChatMessage(
            kb_id=kb_id,
            kb_version_id=course.version_id,
            student_ref=student_ref,
            question=question,
            standalone=standalone,
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
    """👍/👎 on the student's own message; None if it is not theirs (or does not exist).

    A 👎 on an answer sends the question, with what the assistant said, to the teacher's queue
    (reason `flagged`), once per message: the teacher's answer reaches the student like any
    forwarded one, and with a source it joins the course's collected real-question set."""
    message = session.get(ChatMessage, message_id, with_for_update=True)
    if message is None or message.student_ref != student_ref:
        return None
    message.feedback = 1 if helpful else -1
    message.feedback_at = datetime.now(UTC)
    if not helpful and message.forwarded is None and message.teacher_question_id is None:
        asked = message.standalone or message.question
        item = forward_to_teacher(
            session, message.kb_version_id, asked, ForwardReason.flagged, student_ref
        )
        item.ai_answer = message.answer
        message.teacher_question_id = item.id
        notify_teachers(
            session, message.kb_id, "question", {"question": asked[:200], "reason": "flagged"}
        )
    return message
