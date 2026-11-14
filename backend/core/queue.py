"""The teacher's question queue (rule 2): what the clone could not or should not answer."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ForwardReason, KbVersion, TeacherQuestion, TeacherQuestionStatus


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
