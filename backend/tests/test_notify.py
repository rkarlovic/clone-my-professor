"""Notifications: a forwarded question notifies the course's teachers; the teacher's answer
notifies the student who asked (no one else); each reader marks only their own as read."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from core import chat
from tests.test_chat import _events, _fake, _go_live

pytestmark = pytest.mark.db


def test_question_and_answer_notify_the_right_people(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    kb_id, teacher = course
    _go_live(app_engine, kb_id)
    monkeypatch.setattr(chat, "answer_question", _fake(False, []))
    http = TestClient(app)
    me, s1, s2 = (
        {"X-CMP-Dev-Teacher": teacher},
        {"X-CMP-Dev-Student": "s1"},
        {"X-CMP-Dev-Student": "s2"},
    )

    r = http.post(f"/api/chat/{kb_id}/messages", json={"question": "Tko je Vigotski?"}, headers=s1)
    assert _events(r.text)[-1][0] == "answer"
    (n,) = http.get("/api/notifications?unread=true", headers=me).json()
    assert (n["kind"], n["data"]["question"], n["course"]) == ("question", "Tko je Vigotski?", "T")

    (q,) = http.get(f"/api/courses/{kb_id}/questions", headers=me).json()
    http.post(
        f"/api/courses/{kb_id}/questions/{q['id']}/answer", json={"answer": "Psiholog."}, headers=me
    )
    (mine,) = http.get(f"/api/chat/{kb_id}/notifications", headers=s1).json()
    assert mine["kind"] == "answered"
    assert http.get(f"/api/chat/{kb_id}/notifications", headers=s2).json() == []

    # each reader marks only their own
    assert http.post(
        f"/api/chat/{kb_id}/notifications/read", json={"ids": [n["id"]]}, headers=s1
    ).json() == {"read": 0}
    assert http.post(
        f"/api/chat/{kb_id}/notifications/read", json={"all": True}, headers=s1
    ).json() == {"read": 1}
    assert http.get(f"/api/chat/{kb_id}/notifications", headers=s1).json() == []
    assert http.post("/api/notifications/read", json={"ids": [n["id"]]}, headers=me).json() == {
        "read": 1
    }
    assert http.get("/api/notifications?unread=true", headers=me).json() == []
