"""Collected real questions: a 👎 sends the answer to the teacher's queue; answered queue
questions with a source become the course's collected set (searched after every upload, and
exportable in the golden-set format); admin questions stay must-forward."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from core import chat, checks
from core.collected import collected_questions, found, to_eval_set
from core.models import ForwardReason
from core.queue import forward_to_teacher
from retrieval.search import Hit
from scripts.evaluate import GoldenSet
from tests.test_chat import _events, _fake, _go_live

pytestmark = pytest.mark.db


def test_a_thumbs_down_reaches_the_teacher_and_becomes_a_test_question(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    kb_id, teacher_name = course
    vid = _go_live(app_engine, kb_id)  # documents: "7 Identifikacija.pptx"
    monkeypatch.setattr(chat, "answer_question", _fake(True, []))
    http = TestClient(app)
    student, me = {"X-CMP-Dev-Student": "s1"}, {"X-CMP-Dev-Teacher": teacher_name}
    r = http.post(
        f"/api/chat/{kb_id}/messages", json={"question": "Zašto stalno?"}, headers=student
    )
    message = _events(r.text)[-1][1]
    assert not message["forwarded"]

    fb = f"/api/chat/messages/{message['id']}/feedback"
    assert http.post(fb, json={"helpful": False}, headers=student).status_code == 200
    assert http.post(fb, json={"helpful": False}, headers=student).status_code == 200  # once only
    queue = http.get(f"/api/courses/{kb_id}/questions", headers=me).json()
    assert len(queue) == 1
    item = queue[0]
    assert item["reason"] == "flagged" and item["question"] == "Zašto stalno?"
    assert item["ai_answer"] == "Identifikacija je kontinuirani proces [1]."
    assert http.get("/api/notifications", headers=me).json()[0]["kind"] == "question"

    source = {"file_name": "7 Identifikacija.pptx", "page": 16}
    good = {"answer": "Jer se darovitost razvija.", "sources": [source]}
    http.post(f"/api/courses/{kb_id}/questions/{item['id']}/answer", json=good, headers=me)
    # the student sees the teacher's answer under the flagged exchange
    hist = http.get(f"/api/chat/{kb_id}/messages", headers=student).json()
    assert hist[-1]["teacher_answer"] == "Jer se darovitost razvija."

    # an admin question the teacher answered: must keep being forwarded
    with Session(app_engine) as s, s.begin():
        q = forward_to_teacher(s, vid, "Kad je kolokvij?", ForwardReason.admin, "dev:s2")
        q.status, q.answer = "answered", "U petak."

    with Session(app_engine) as s:
        items = collected_questions(s, kb_id, {"7 Identifikacija.pptx"})
        gone = collected_questions(s, kb_id, {"druga.pdf"})  # the file left the course
    assert [(c.kind, c.question) for c in items] == [
        ("answerable", "Zašto stalno?"),
        ("forward", "Kad je kolokvij?"),
    ]
    assert items[0].sources == (("7 Identifikacija.pptx", 16),)
    assert [c.kind for c in gone] == ["forward"]

    # the export is a valid question set for scripts.evaluate (no student reference in it)
    exported = to_eval_set(items, "T")
    GoldenSet.model_validate(exported)
    assert "dev:" not in str(exported)

    # the check after an upload searches them too
    def search_fn(version_id, text, k):
        page = 16 if text == "Zašto stalno?" else 1
        return [Hit(uuid.uuid4(), 0.5, "t", "t", "", "7 Identifikacija.pptx", page, "other")]

    report = checks.run_checks(vid, total=0, search_fn=search_fn)
    expected = {"questions": 1, "found_top5": 1, "recall_at_5": 1.0, "misses": []}
    assert report["collected"] == expected


def test_found_accepts_a_whole_file_when_no_slide_was_given() -> None:
    hit = Hit(uuid.uuid4(), 0.5, "t", "t", "", "a.pdf", 3, "other", also_on=[9])
    assert found((("a.pdf", None),), [hit]) == 1
    assert found((("a.pdf", 9),), [hit]) == 1
    assert found((("a.pdf", 4),), [hit]) is None
    assert found((("b.pdf", None),), [hit]) is None
