"""The teacher's question queue (rule 2): what the clone could not or should not answer.

The teacher answers (optionally pointing to the source: file + page, which makes the question
part of the course's collected real-question set) or dismisses. The student sees the answer
under their question in the chat history. The teacher never sees who asked (rule 9).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.approval import active_version
from core.db import scope_transaction
from core.models import Document, ForwardReason, KbVersion, TeacherQuestion, TeacherQuestionStatus


class QueueError(ValueError):
    """The step is not allowed (wrong state, unknown source); the message says why."""


@dataclass(frozen=True)
class SourceRef:
    file_name: str
    page: int | None = None


def forward_to_teacher(
    session: Session,
    kb_version_id: uuid.UUID,
    question: str,
    reason: ForwardReason,
    student_ref: str | None = None,
) -> TeacherQuestion:
    """Queue `question` for the teacher of the version's knowledge base. The caller commits."""
    kb_id = session.scalar(select(KbVersion.kb_id).where(KbVersion.id == kb_version_id))
    if kb_id is None:
        raise LookupError(f"no kb_version {kb_version_id}")
    item = TeacherQuestion(
        kb_id=kb_id,
        kb_version_id=kb_version_id,
        student_ref=student_ref,
        question=question,
        reason=reason,
    )
    session.add(item)
    session.flush()
    return item


def open_questions(session: Session, kb_id: uuid.UUID) -> list[TeacherQuestion]:
    return list(
        session.scalars(
            select(TeacherQuestion)
            .where(
                TeacherQuestion.kb_id == kb_id,
                TeacherQuestion.status == TeacherQuestionStatus.open,
            )
            .order_by(TeacherQuestion.created_at)
        )
    )


def list_questions(
    session: Session, kb_id: uuid.UUID, status: TeacherQuestionStatus | None = None
) -> list[TeacherQuestion]:
    """The course's queue, oldest first; all statuses when `status` is None."""
    q = select(TeacherQuestion).where(TeacherQuestion.kb_id == kb_id)
    if status is not None:
        q = q.where(TeacherQuestion.status == status)
    return list(session.scalars(q.order_by(TeacherQuestion.created_at)))


def _course_files(session: Session, item: TeacherQuestion) -> set[str]:
    """File names the teacher may point to: those of the version the student asked and of the
    course's active version (the material may have changed since)."""
    names: set[str] = set()
    active = active_version(session, item.kb_id)
    for vid in {item.kb_version_id, active.id if active else None} - {None}:
        scope_transaction(session, vid)
        names |= set(session.scalars(select(Document.file_name)))
    return names


def answer_forwarded(
    session: Session,
    item: TeacherQuestion,
    teacher_id: uuid.UUID,
    answer: str,
    sources: list[SourceRef] = (),
) -> TeacherQuestion:
    """Answer (or correct an earlier answer to) a forwarded question."""
    text = answer.strip()
    if not text:
        raise QueueError("the answer is empty")
    if sources:
        known = _course_files(session, item)
        unknown = sorted({s.file_name for s in sources} - known)
        if unknown:
            raise QueueError(f"not a file of this course: {', '.join(unknown)}")
        if any(s.page is not None and s.page < 1 for s in sources):
            raise QueueError("page numbers start at 1")
    item.answer = text
    item.answer_sources = [{"file_name": s.file_name, "page": s.page} for s in sources]
    item.status = TeacherQuestionStatus.answered
    item.answered_at, item.answered_by = datetime.now(UTC), teacher_id
    # the student who asked sees it in the chat (by the stored exchange, not by identity)
    from core.models import ChatMessage
    from core.notify import notify_student

    asked = session.scalars(
        select(ChatMessage).where(ChatMessage.teacher_question_id == item.id)
    ).first()
    if asked is not None:
        notify_student(
            session, item.kb_id, asked.student_ref, "answered", {"message_id": str(asked.id)}
        )
    session.flush()
    return item


def dismiss_forwarded(session: Session, item: TeacherQuestion) -> TeacherQuestion:
    """No answer needed (a duplicate, not a question). Only open questions."""
    if item.status != TeacherQuestionStatus.open:
        raise QueueError(f"the question is {item.status.value}, not open")
    item.status = TeacherQuestionStatus.dismissed
    session.flush()
    return item
