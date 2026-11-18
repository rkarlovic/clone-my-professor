"""The course's collected real questions (CLAUDE.md "Evaluation strategy"): questions students
really asked, with the source the teacher pointed to while answering them in the queue.

    answerable  a forwarded or flagged (👎) question the teacher answered with a source (file +
                optional slide) that is still in the version being checked: search should find it
    forward     an `admin` question the teacher answered (deadlines, grades…): must keep being
                forwarded, whatever the material says (rule 2)

Nobody writes or marks anything for this: it grows from the teacher's normal work. It holds
student text, so it never leaves the installation and follows the same retention as the queue
(RETENTION_DAYS): the set is a rolling window of the last months' questions. No student
reference is ever part of it.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ForwardReason, TeacherQuestion, TeacherQuestionStatus


@dataclass(frozen=True)
class Collected:
    id: uuid.UUID
    question: str
    kind: str  # answerable | forward
    sources: tuple[tuple[str, int | None], ...]  # (file_name, page or None = any page)


def collected_questions(session: Session, kb_id: uuid.UUID, files: set[str]) -> list[Collected]:
    """The set for a version whose documents are `files` (by file name)."""
    rows = session.scalars(
        select(TeacherQuestion)
        .where(
            TeacherQuestion.kb_id == kb_id,
            TeacherQuestion.status == TeacherQuestionStatus.answered,
        )
        .order_by(TeacherQuestion.answered_at)
    ).all()
    out: list[Collected] = []
    for q in rows:
        if q.reason == ForwardReason.admin:
            out.append(Collected(q.id, q.question, "forward", ()))
            continue
        sources = tuple(
            (s["file_name"], s.get("page")) for s in q.answer_sources if s["file_name"] in files
        )
        if sources:
            out.append(Collected(q.id, q.question, "answerable", sources))
    return out


def found(sources: tuple[tuple[str, int | None], ...], hits) -> int | None:
    """Rank (1-based) of the first hit on any of the teacher's sources; None if none."""
    for i, h in enumerate(hits, 1):
        for file_name, page in sources:
            if h.file_name == file_name and (page is None or page in h.pages):
                return i
    return None


def to_eval_set(items: list[Collected], subject: str) -> dict:
    """The golden-set format (eval/sets), for scripts.evaluate / evaluate_answers --set."""
    questions = []
    for i, c in enumerate(items, 1):
        q = {"id": f"r{i:03d}", "difficulty": "collected", "question": c.question}
        if c.kind == "forward":
            q |= {"type": "admin", "expected_sources": []}
        else:
            q |= {
                "type": "answerable",
                "expected_sources": [
                    {"document": f} | ({"page_or_slide": p} if p is not None else {})
                    for f, p in c.sources
                ],
            }
        questions.append(q)
    return {
        "subject_id": subject,
        "subject_name": subject,
        "language": "hr",
        "questions": questions,
    }
