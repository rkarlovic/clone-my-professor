"""Data retention (rule 9: store only what is needed, for as long as it is needed).

A daily job deletes, older than `RETENTION_DAYS` (default 180, about a semester):
- chat exchanges (student text) and their notifications;
- forwarded questions the teacher answered or dismissed (open ones stay until handled);
plus read notifications after 30 days and LTI login states after a day. Course material,
versions and the check questions are course content, not personal data: they stay.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from core.db import app_engine
from core.models import (
    ChatMessage,
    LtiLoginState,
    Notification,
    SsoLoginState,
    TeacherQuestion,
    TeacherQuestionStatus,
)
from core.settings import get_settings

log = logging.getLogger(__name__)
READ_NOTIFICATION_DAYS = 30


def purge(now: datetime | None = None, days: int | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    days = days if days is not None else get_settings().retention_days
    cutoff = now - timedelta(days=days)
    done: dict[str, int] = {}
    with Session(app_engine()) as s, s.begin():
        old_questions = select(TeacherQuestion.id).where(
            TeacherQuestion.created_at < cutoff,
            TeacherQuestion.status != TeacherQuestionStatus.open,
        )
        # chat rows point at questions: unlink before deleting either
        s.execute(
            update(ChatMessage)
            .where(ChatMessage.teacher_question_id.in_(old_questions))
            .values(teacher_question_id=None)
        )
        done["chat_messages"] = s.execute(
            delete(ChatMessage).where(ChatMessage.created_at < cutoff)
        ).rowcount
        done["teacher_questions"] = s.execute(
            delete(TeacherQuestion).where(TeacherQuestion.id.in_(old_questions))
        ).rowcount
        done["notifications"] = s.execute(
            delete(Notification).where(
                (Notification.created_at < cutoff)
                | (
                    Notification.read_at.is_not(None)
                    & (Notification.read_at < now - timedelta(days=READ_NOTIFICATION_DAYS))
                )
            )
        ).rowcount
        done["lti_login_states"] = s.execute(
            delete(LtiLoginState).where(LtiLoginState.created_at < now - timedelta(days=1))
        ).rowcount
        done["sso_login_states"] = s.execute(
            delete(SsoLoginState).where(SsoLoginState.created_at < now - timedelta(days=1))
        ).rowcount
    log.info("retention (%d days): %s", days, done)
    return done
