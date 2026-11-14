"""Rule 2: forwarded questions land in the queue of the right knowledge base."""

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core.models import ForwardReason, KbVersion, TeacherQuestionStatus
from core.queue import forward_to_teacher, open_questions

pytestmark = pytest.mark.db


def test_forwarded_question_goes_to_its_own_kb_queue(app_engine: Engine, two_kb_versions) -> None:
    v_a, v_b = two_kb_versions
    with Session(app_engine) as s, s.begin():
        forward_to_teacher(s, v_a, "Kad je rok za zadaću?", ForwardReason.admin, "stu-1")
    with Session(app_engine) as s:
        kb_a, kb_b = (
            s.scalar(select(KbVersion.kb_id).where(KbVersion.id == v)) for v in (v_a, v_b)
        )
        (item,) = open_questions(s, kb_a)
        assert (item.question, item.reason, item.status) == (
            "Kad je rok za zadaću?",
            ForwardReason.admin,
            TeacherQuestionStatus.open,
        )
        assert item.kb_version_id == v_a and item.student_ref == "stu-1"
        assert open_questions(s, kb_b) == []
