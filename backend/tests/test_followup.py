"""Chat follow-ups are rewritten to stand on their own before answering; students have an hourly
question limit per course."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core import chat, followup
from core.models import ChatMessage, TeacherQuestion
from core.settings import get_settings
from tests.test_chat import _events, _fake, _go_live


def test_only_short_recent_questions_are_rewritten() -> None:
    now = datetime.now(UTC)
    assert not followup.needs_rewrite("a drugi?", None, now)  # nothing before
    assert followup.needs_rewrite("a drugi?", now - timedelta(minutes=5), now)
    assert not followup.needs_rewrite("a drugi?", now - timedelta(hours=2), now)
    long_q = " ".join(["riječ"] * 20)
    assert not followup.needs_rewrite(long_q, now - timedelta(minutes=5), now)


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("Koji je drugi stup identifikacije?", "Koji je drugi stup identifikacije?"),
        ('„Koji je drugi stup identifikacije?"', "Koji je drugi stup identifikacije?"),
        ("Pitanje: Koji je drugi stup?", "Koji je drugi stup?"),
        ("a drugi?", None),  # unchanged: used as asked
        ("", None),
        ("Prvi red\nDrugi red", None),  # not one line: not trusted
        ("x" * 500, None),  # far longer than the question: not trusted
    ],
)
def test_rewrite_replies_are_checked(reply, expected) -> None:
    seen = {}

    def complete_fn(role, messages):
        seen["role"], seen["text"] = role, messages[0]["content"]
        return reply

    history = [("Što je identifikacija?", "Identifikacija je kontinuirani proces [1].")]
    assert followup.rewrite("Darovitost", history, "a drugi?", complete_fn) == expected
    assert seen["role"] == "fast" and "[1]" not in seen["text"] and "a drugi?" in seen["text"]


@pytest.mark.db
def test_a_follow_up_is_answered_and_forwarded_in_its_rewritten_form(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id, _ = course
    _go_live(app_engine, kb_id)
    asked: list[str] = []

    def answer_fn(version_id, question, on_stage=None, **kw):
        asked.append(question)
        forwarded = len(asked) == 2
        return _fake(not forwarded, [])(version_id, question, on_stage=lambda _s: None, **kw)

    stages: list[str] = []
    rewrite = lambda subject, exchanges, q: f"{exchanges[-1][0]} – {q}"  # noqa: E731
    chat.ask(kb_id, "dev:s1", "Što je identifikacija?", answer_fn=answer_fn, rewrite_fn=rewrite)
    m = chat.ask(
        kb_id, "dev:s1", "a zašto?", on_stage=stages.append, answer_fn=answer_fn, rewrite_fn=rewrite
    )
    assert asked == ["Što je identifikacija?", "Što je identifikacija? – a zašto?"]
    assert stages[0] == "followup"
    assert m.question == "a zašto?" and m.standalone == "Što je identifikacija? – a zašto?"
    with Session(app_engine) as s:
        q = s.scalars(select(TeacherQuestion).where(TeacherQuestion.kb_id == kb_id)).one()
        assert q.question == "Što je identifikacija? – a zašto?"  # readable without the chat
    # another student's follow-up is not linked to this conversation
    chat.ask(kb_id, "dev:s2", "a zašto?", answer_fn=answer_fn, rewrite_fn=rewrite)
    assert asked[-1] == "a zašto?"


@pytest.mark.db
def test_the_hourly_limit(app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    kb_id, _ = course
    vid = _go_live(app_engine, kb_id)
    monkeypatch.setattr(get_settings(), "chat_questions_per_hour", 2)
    monkeypatch.setattr(chat, "answer_question", _fake(True, []))
    monkeypatch.setattr(followup, "needs_rewrite", lambda *a: False)
    with Session(app_engine) as s, s.begin():  # one older exchange does not count
        s.add(
            ChatMessage(
                kb_id=kb_id,
                kb_version_id=vid,
                student_ref="dev:s1",
                question="staro",
                answer="x",
                created_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
    http = TestClient(app)
    student = {"X-CMP-Dev-Student": "s1"}
    url = f"/api/chat/{kb_id}/messages"
    for _ in range(2):
        r = http.post(url, json={"question": "Što je identifikacija?"}, headers=student)
        assert _events(r.text)[-1][0] == "answer"
    r = http.post(url, json={"question": "Još jedno?"}, headers=student)
    assert r.status_code == 429 and "min" in r.json()["detail"]
    other = http.post(url, json={"question": "Moje prvo?"}, headers={"X-CMP-Dev-Student": "s2"})
    assert _events(other.text)[-1][0] == "answer"  # per student
