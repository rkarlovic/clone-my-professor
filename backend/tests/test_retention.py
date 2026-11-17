"""Retention (rule 9): old chat exchanges and handled questions go; open questions stay."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core.models import (
    ChatMessage,
    ForwardReason,
    KbVersion,
    TeacherQuestion,
    TeacherQuestionStatus,
)
from core.retention import purge
from tests.conftest import ready_version_with

pytestmark = pytest.mark.db


def test_old_personal_data_is_deleted_open_questions_stay(app_engine: Engine, course) -> None:
    kb_id, _ = course
    vid = ready_version_with(app_engine, kb_id, {"a.pdf": "h"})
    old = datetime.now(UTC) - timedelta(days=200)
    with Session(app_engine) as s, s.begin():
        handled = TeacherQuestion(
            kb_id=kb_id,
            kb_version_id=vid,
            question="old answered",
            reason=ForwardReason.no_source,
            status=TeacherQuestionStatus.answered,
            created_at=old,
        )
        waiting = TeacherQuestion(
            kb_id=kb_id,
            kb_version_id=vid,
            question="old open",
            reason=ForwardReason.no_source,
            created_at=old,
        )
        s.add_all([handled, waiting])
        s.flush()
        for q, when in ((handled, old), (waiting, old), (None, datetime.now(UTC))):
            s.add(
                ChatMessage(
                    kb_id=kb_id,
                    kb_version_id=vid,
                    student_ref="s",
                    question="q",
                    answer="a",
                    teacher_question_id=q.id if q else None,
                    created_at=when,
                )
            )
    done = purge()
    assert done["chat_messages"] >= 2 and done["teacher_questions"] >= 1
    with Session(app_engine) as s:
        assert [
            q.question
            for q in s.scalars(select(TeacherQuestion).where(TeacherQuestion.kb_id == kb_id))
        ] == ["old open"]
        assert len(s.scalars(select(ChatMessage).where(ChatMessage.kb_id == kb_id)).all()) == 1
        assert s.get(KbVersion, vid) is not None  # course content stays
