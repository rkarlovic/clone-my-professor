"""The teacher's persona: rendering, drafts, the activation check (rule 2 must survive any
persona), one active persona, and the chat answering with it."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from core import chat
from core.answer import FORWARD_MESSAGES, Answer, Source
from core.models import CheckQuestion, ForwardReason, KnowledgeBase, PersonaConfig
from core.persona import (
    PersonaError,
    PersonaSettings,
    check_persona,
    render,
    save_draft,
    start_activation,
)
from tests.test_chat import _events, _go_live

DOC = "7 Identifikacija.pptx"


def test_render_turns_settings_into_instructions() -> None:
    text = render(
        PersonaSettings(address="ti", answer_length="short", forward_topics=["projekt", " "]),
        "Budi strpljiv.",
    )
    assert "„ti”" in text and "kratko" in text
    assert "NE_ZNAM: projekt." in text  # forwarded topics go to the teacher
    assert text.endswith("- Dodatno: Budi strpljiv.")
    default = render(PersonaSettings())
    assert "„Vi”" in default and "Dodatno" not in default and "NE_ZNAM" not in default


def _teacher(app_engine: Engine, kb_id):
    with Session(app_engine) as s:
        return s.get(KnowledgeBase, kb_id).teacher_id


def _draft(app_engine: Engine, kb_id, **settings) -> PersonaConfig:
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        return save_draft(s, kb_id, _teacher(app_engine, kb_id), PersonaSettings(**settings), "")


def _add_checks(app_engine: Engine, kb_id, n: int = 3) -> None:
    with Session(app_engine) as s, s.begin():
        for page in range(1, n + 1):
            s.add(
                CheckQuestion(
                    kb_id=kb_id,
                    question=f"Pitanje {page}?",
                    style="casual",
                    file_name=DOC,
                    file_hash="h",
                    page=page,
                )
            )


def _fake(answer_generic: bool = False, seen: list | None = None):
    """Answers check questions citing their slide; forwards the generic ones unless
    `answer_generic` (a persona that breaks rule 2)."""

    def answer_fn(version_id, question, persona_text=None, persona_version=None, **_):
        if seen is not None:
            seen.append((persona_text, persona_version))
        if question.startswith("Pitanje") or answer_generic:
            page = int(question.split()[1].rstrip("?")) if question.startswith("Pitanje") else 1
            src = Source(1, DOC, [page], "t", 0.7)
            return Answer(question, "Odgovor [1].", None, [src], [src])
        return Answer(question, FORWARD_MESSAGES[ForwardReason.admin], ForwardReason.admin)

    return answer_fn


@pytest.mark.db
def test_drafts_and_activation_rules(app_engine: Engine, course) -> None:
    kb_id, _ = course
    p1 = _draft(app_engine, kb_id, address="ti")
    p1_again = _draft(app_engine, kb_id, address="vi")  # the open draft is updated
    assert p1_again.id == p1.id and p1_again.settings["address"] == "vi"

    with (
        Session(app_engine) as s,
        s.begin(),
        pytest.raises(PersonaError, match="publish the course materials"),
    ):
        start_activation(s, s.get(PersonaConfig, p1.id))
    _go_live(app_engine, kb_id)
    with Session(app_engine) as s, s.begin():
        assert start_activation(s, s.get(PersonaConfig, p1.id)).status == "checking"
    p2 = _draft(app_engine, kb_id, answer_length="long")
    assert p2.version == 2
    with (
        Session(app_engine) as s,
        s.begin(),
        pytest.raises(PersonaError, match="still being checked"),
    ):
        start_activation(s, s.get(PersonaConfig, p2.id))


@pytest.mark.db
def test_check_activates_a_safe_persona_and_retires_the_old_one(app_engine: Engine, course) -> None:
    kb_id, _ = course
    _go_live(app_engine, kb_id)
    _add_checks(app_engine, kb_id)
    seen: list = []
    personas = []
    for address in ("vi", "ti"):
        p = _draft(app_engine, kb_id, address=address)
        with Session(app_engine) as s, s.begin():
            start_activation(s, s.get(PersonaConfig, p.id))
        report = check_persona(p.id, answer_fn=_fake(seen=seen))
        assert report["passed"] and report["answered"] == 3 and report["cites_expected"] == 3
        assert report["forwarded"] == report["generic"] == 12
        personas.append(p.id)
    with Session(app_engine) as s:
        assert [s.get(PersonaConfig, i).status for i in personas] == ["retired", "active"]
    assert all("„ti”" in text for text, version in seen[-15:] if version == 2)


@pytest.mark.db
def test_check_rejects_a_persona_that_stops_forwarding(app_engine: Engine, course) -> None:
    kb_id, _ = course
    _go_live(app_engine, kb_id)
    _add_checks(app_engine, kb_id)
    p = _draft(app_engine, kb_id)
    with Session(app_engine) as s, s.begin():
        start_activation(s, s.get(PersonaConfig, p.id))
    report = check_persona(p.id, answer_fn=_fake(answer_generic=True))
    assert not report["passed"] and report["forwarded"] == 0
    assert "proslijeđeno 0 od 12" in report["reasons"][0]
    with Session(app_engine) as s:
        assert s.get(PersonaConfig, p.id).status == "rejected"


@pytest.mark.db
def test_chat_answers_with_the_active_persona_and_the_api_flow(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import persona as persona_api
    from app.main import app

    kb_id, teacher = course
    _go_live(app_engine, kb_id)
    _add_checks(app_engine, kb_id)
    queued: list = []
    monkeypatch.setattr(persona_api, "enqueue_persona_check", lambda pid, kb: queued.append(pid))
    http, me = TestClient(app), {"X-CMP-Dev-Teacher": teacher}
    other = {"X-CMP-Dev-Teacher": teacher + "-other"}
    url = f"/api/courses/{kb_id}/persona"
    body = {
        "settings": {"address": "ti", "forward_topics": ["projekt"]},
        "free_text": "Budi kratak.",
    }

    assert http.put(url, json=body, headers=other).status_code == 404
    r = http.put(url, json=body, headers=me)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert "NE_ZNAM: projekt" in r.json()["prompt_text"]
    bad = {"settings": {"address": "vas"}, "free_text": ""}
    assert http.put(url, json=bad, headers=me).status_code == 422
    r = http.post(f"{url}/1/activate", headers=me)
    assert r.status_code == 202 and r.json()["status"] == "checking" and len(queued) == 1
    assert http.post(f"{url}/1/activate", headers=me).status_code == 409

    check_persona(queued[0], answer_fn=_fake())  # what the worker would do
    assert http.get(url, headers=me).json()[0]["status"] == "active"

    seen: list = []
    monkeypatch.setattr(chat, "answer_question", _fake(seen=seen))
    r = http.post(
        f"/api/chat/{kb_id}/messages",
        json={"question": "Pitanje 1?"},
        headers={"X-CMP-Dev-Student": "s1"},
    )
    assert _events(r.text)[-1][0] == "answer"
    text, version = seen[-1]
    assert version == 1 and "„ti”" in text and "Budi kratak." in text
