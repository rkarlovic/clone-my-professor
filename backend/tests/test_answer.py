"""Answer pipeline pieces that need no model: citation check, sources, scope labels, prompts."""

import uuid

import pytest

import core.answer as answer
from core.answer import Source, build_sources, check_citations, scope_check
from core.models import ForwardReason
from core.settings import REPO_ROOT
from llm.config import load_model_config
from llm.prompts import DONT_KNOW, PROMPTS, prompt
from retrieval.search import Hit


def _sources(n: int) -> list[Source]:
    return [
        Source(n=i, file_name="a.pdf", pages=[i * 10], text=f"t{i}", score=0.9)
        for i in range(1, n + 1)
    ]


def test_answer_with_valid_citations_is_kept() -> None:
    text, cited = check_citations(
        "Dretva blokira sučelje [1]. Rješenje je SwingWorker [2, 3].", _sources(3)
    )
    assert text == "Dretva blokira sučelje [1]. Rješenje je SwingWorker [2][3]."
    assert [s.n for s in cited] == [1, 2, 3]


def test_citations_to_missing_sources_are_dropped() -> None:
    text, cited = check_citations("Tvrdnja [2][7].", _sources(2))
    assert text == "Tvrdnja [2]."
    assert [s.n for s in cited] == [2]


@pytest.mark.parametrize(
    "raw",
    [
        DONT_KNOW,
        f"Djelomičan odgovor [1].\n\n{DONT_KNOW}",  # partial answer + marker: still forwarded
        "ne znam",  # marker written with a space, lower case
        "Odgovor bez ijednog izvora.",  # rule 1: no citation, no answer
        "Samo nepostojeći izvor [9].",
        "",
    ],
)
def test_ungrounded_replies_are_forwarded(raw: str) -> None:
    assert check_citations(raw, _sources(3)) == (None, [])


def _hit(page: int, parent: str | None = None) -> Hit:
    return Hit(
        chunk_id=uuid.uuid4(),
        score=1.0 / page,
        text=f"slide {page}",
        context_text=parent or f"slide {page}",
        heading_path="",
        file_name="a.pdf",
        page=page,
        content_type="other",
    )


def test_hits_sharing_a_parent_become_one_source() -> None:
    sources = build_sources([_hit(5, "parent"), _hit(7), _hit(6, "parent")])
    assert [(s.n, s.pages) for s in sources] == [(1, [5, 6]), (2, [7])]
    assert sources[0].citation == "a.pdf, str. 5, 6"


@pytest.mark.parametrize(
    ("reply", "label", "reason"),
    [
        ("GRADIVO", "GRADIVO", None),
        ("admin", "ADMIN", ForwardReason.admin),
        ("Odgovor: DRUGO.", "DRUGO", ForwardReason.out_of_scope),
        ("hmm, nisam siguran", "?", None),  # unclear: let it through, later checks still guard
    ],
)
def test_scope_labels(monkeypatch: pytest.MonkeyPatch, reply, label, reason) -> None:
    monkeypatch.setattr(answer, "complete", lambda role, messages: reply)
    assert scope_check("Predmet", ["Tema"], "pitanje") == (label, reason)


def test_prompt_override_is_per_model() -> None:
    cfg = load_model_config(REPO_ROOT / "config" / "models.yaml")
    assert prompt("answer_user", "answer", cfg) == PROMPTS["answer_user"]
    cfg.prompt_overrides = {cfg.role("answer").model: {"answer_user": "variant"}}
    assert prompt("answer_user", "answer", cfg) == "variant"
    assert prompt("scope_check", "fast", cfg) == PROMPTS["scope_check"]  # other prompt untouched


def test_answer_prompt_formats() -> None:
    system = PROMPTS["answer_system"].format(subject="Predmet")
    assert "Predmet" in system and DONT_KNOW in system
    PROMPTS["answer_user"].format(sources="[1] x", question="q")
    PROMPTS["scope_check"].format(subject="s", topics="t", question="q")
