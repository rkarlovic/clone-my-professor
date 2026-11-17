"""The teacher's question queue: list, answer (with sources checked against the course),
dismiss; the student sees the teacher's answer; the teacher never sees who asked (rule 9)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from core import chat
from tests.test_chat import _events, _fake, _go_live

pytestmark = pytest.mark.db


def _ask(http, kb_id, question: str, student: str = "s1") -> dict:
    r = http.post(
        f"/api/chat/{kb_id}/messages",
        json={"question": question},
        headers={"X-CMP-Dev-Student": student},
    )
    return _events(r.text)[-1][1]


def test_teacher_answers_a_forwarded_question_and_the_student_sees_it(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    kb_id, teacher_name = course
    _go_live(app_engine, kb_id)  # documents: "7 Identifikacija.pptx"
    monkeypatch.setattr(chat, "answer_question", _fake(False, []))
    http = TestClient(app)
    me, other = {"X-CMP-Dev-Teacher": teacher_name}, {"X-CMP-Dev-Teacher": teacher_name + "-other"}
    url = f"/api/courses/{kb_id}/questions"

    _ask(http, kb_id, "Tko je Vigotski?")
    _ask(http, kb_id, "asdf", student="s2")
    open_items = http.get(url, headers=me).json()
    assert [q["question"] for q in open_items] == ["Tko je Vigotski?", "asdf"]
    assert open_items[0]["reason"] == "no_source" and open_items[0]["reason_text"]
    assert not any("student" in key for key in open_items[0])  # rule 9: not who asked
    assert http.get(url, headers=other).status_code == 404
    vig, junk = open_items[0]["id"], open_items[1]["id"]

    bad = {"answer": "Ruski psiholog.", "sources": [{"file_name": "nema.pdf", "page": 1}]}
    r = http.post(f"{url}/{vig}/answer", json=bad, headers=me)
    assert r.status_code == 422 and "nema.pdf" in r.json()["detail"]
    assert http.post(f"{url}/{vig}/answer", json={"answer": "x"}, headers=other).status_code == 404
    good = {
        "answer": "Lav Vigotski je ruski psiholog; vidi priručnik, str. 12.",
        "sources": [{"file_name": "7 Identifikacija.pptx", "page": 4}],
    }
    r = http.post(f"{url}/{vig}/answer", json=good, headers=me)
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert r.json()["answer_sources"] == [{"file_name": "7 Identifikacija.pptx", "page": 4}]

    assert http.post(f"{url}/{vig}/dismiss", headers=me).status_code == 409  # already answered
    assert http.post(f"{url}/{junk}/dismiss", headers=me).json()["status"] == "dismissed"
    assert http.get(url, headers=me).json() == []  # nothing open
    assert len(http.get(url + "?status=all", headers=me).json()) == 2

    (mine,) = http.get(f"/api/chat/{kb_id}/messages", headers={"X-CMP-Dev-Student": "s1"}).json()
    assert mine["forwarded"] is True and mine["teacher_answer"] == good["answer"]
    (theirs,) = http.get(f"/api/chat/{kb_id}/messages", headers={"X-CMP-Dev-Student": "s2"}).json()
    assert theirs["teacher_answer"] is None  # dismissed: no answer
