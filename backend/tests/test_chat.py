"""Student chat: only the active version answers (rule 5), every reply discloses the AI (rule 6),
forwarded questions reach the teacher's queue (rule 2), students see only their own messages.
The model is replaced by a fake answer function; the pipeline itself is tested elsewhere."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core import chat
from core.answer import FORWARD_MESSAGES, Answer, Source
from core.approval import activate_version, approve_version
from core.models import ForwardReason, KbVersion, KnowledgeBase, TeacherQuestion
from tests.conftest import ready_version_with

pytestmark = pytest.mark.db


def _go_live(app_engine: Engine, kb_id: uuid.UUID, version: int = 1) -> uuid.UUID:
    vid = ready_version_with(app_engine, kb_id, {"7 Identifikacija.pptx": "h"}, version=version)
    with Session(app_engine) as s, s.begin():
        teacher = s.get(KnowledgeBase, kb_id).teacher_id
        approve_version(s, s.get(KbVersion, vid), teacher)
    with Session(app_engine) as s, s.begin():
        activate_version(s, s.get(KbVersion, vid))
    return vid


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _fake(answered: bool, seen: list):
    def answer_fn(version_id, question, on_stage=None):
        seen.append(version_id)
        for stage in ("scope", "search", "answer", "check"):
            on_stage(stage)
        if not answered:
            return Answer(
                question, FORWARD_MESSAGES[ForwardReason.no_source], ForwardReason.no_source
            )
        src = Source(1, "7 Identifikacija.pptx", [4, 5], "tekst", 0.7)
        return Answer(question, "Identifikacija je kontinuirani proces [1].", None, [src], [src])

    return answer_fn


@pytest.fixture
def http():
    from app.main import app

    return TestClient(app)  # no `with`: the job queue (lifespan) is not needed for chat


def test_chat_waits_until_the_teacher_publishes(http, app_engine: Engine, course) -> None:
    kb_id, _ = course
    me = {"X-CMP-Dev-Student": "s1"}
    ready_version_with(app_engine, kb_id, {"a.pdf": "h"})  # processed, but only a draft
    info = http.get(f"/api/chat/{kb_id}", headers=me).json()
    assert info["live"] is False and "AI" in info["ai_notice"]
    r = http.post(f"/api/chat/{kb_id}/messages", json={"question": "Što je?"}, headers=me)
    assert r.status_code == 409
    assert http.post(f"/api/chat/{kb_id}/messages", json={"question": "x"}).status_code == 401
    assert http.get(f"/api/chat/{uuid.uuid4()}", headers=me).status_code == 404


def test_answer_streams_notice_stages_then_the_checked_answer(
    http, app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id, _ = course
    live = _go_live(app_engine, kb_id)
    seen: list = []
    monkeypatch.setattr(chat, "answer_question", _fake(True, seen))
    me, other = {"X-CMP-Dev-Student": "s1"}, {"X-CMP-Dev-Student": "s2"}

    r = http.post(
        f"/api/chat/{kb_id}/messages", json={"question": "Što je identifikacija?"}, headers=me
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0] == ("notice", {"ai_notice": chat.AI_NOTICE})  # rule 6, first thing sent
    assert [e[1]["stage"] for e in events if e[0] == "stage"] == [
        "scope",
        "search",
        "answer",
        "check",
    ]
    kind, answer = events[-1]
    assert kind == "answer" and answer["forwarded"] is False
    assert answer["citations"] == [{"n": 1, "file_name": "7 Identifikacija.pptx", "pages": [4, 5]}]
    assert seen == [live]  # answered from the active version

    (mine,) = http.get(f"/api/chat/{kb_id}/messages", headers=me).json()
    assert mine["id"] == answer["id"] and mine["question"] == "Što je identifikacija?"
    assert http.get(f"/api/chat/{kb_id}/messages", headers=other).json() == []
    url = f"/api/chat/messages/{answer['id']}/feedback"
    assert http.post(url, json={"helpful": False}, headers=other).status_code == 404
    assert http.post(url, json={"helpful": False}, headers=me).json()["feedback"] == -1


def test_forwarded_question_reaches_the_teacher_queue(
    http, app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id, _ = course
    live = _go_live(app_engine, kb_id)
    monkeypatch.setattr(chat, "answer_question", _fake(False, []))
    me = {"X-CMP-Dev-Student": "s1"}
    r = http.post(f"/api/chat/{kb_id}/messages", json={"question": "Tko je Vigotski?"}, headers=me)
    kind, answer = _events(r.text)[-1]
    assert kind == "answer" and answer["forwarded"] is True and answer["citations"] == []
    assert "proslijeđeno nastavniku" in answer["answer"]
    with Session(app_engine) as s:
        (item,) = s.scalars(select(TeacherQuestion).where(TeacherQuestion.kb_id == kb_id)).all()
        assert (item.question, item.reason, item.kb_version_id) == (
            "Tko je Vigotski?",
            ForwardReason.no_source,
            live,
        )
        assert item.student_ref == "dev:s1"  # opaque, never a name


def test_a_model_failure_tells_the_student_without_details(
    http, app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id, _ = course
    _go_live(app_engine, kb_id)

    def broken(*_, **__):
        raise ConnectionError("http://127.0.0.1:11434 refused")

    monkeypatch.setattr(chat, "answer_question", broken)
    r = http.post(
        f"/api/chat/{kb_id}/messages",
        json={"question": "Što je?"},
        headers={"X-CMP-Dev-Student": "s"},
    )
    kind, data = _events(r.text)[-1]
    assert kind == "error" and "11434" not in data["detail"]
