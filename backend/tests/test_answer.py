"""Answer pipeline pieces that need no model: citation check, sources, scope labels, prompts."""

import uuid

import pytest

import core.answer as answer
from core.answer import (
    Source,
    build_sources,
    check_citations,
    check_fails,
    is_mostly_refusal,
    is_prose_refusal,
    parse_verdicts,
    scope_check,
)
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


@pytest.mark.parametrize(
    "question",
    [
        "Koliko bodova trebam skupiti za četvorku?",
        "Kada je rok za predaju domaće zadaće 7?",
        "Kad je kolokvij?",
        "Kako se računa ocjena?",
    ],
)
def test_admin_terms_forward_without_the_model(monkeypatch: pytest.MonkeyPatch, question) -> None:
    def no_model(role, messages):
        raise AssertionError("the model must not be asked")

    monkeypatch.setattr(answer, "complete", no_model)
    assert scope_check("Predmet", ["Tema"], question) == ("ADMIN_TERM", ForwardReason.admin)


@pytest.mark.parametrize(
    "question",
    [
        "Što je imitiranje objekata (mocking) u testiranju?",
        "Kako upisati tekst u datoteku?",
        "Što se dogodi ako test ne prođe u JUnitu?",
        "Kako proći kroz listu iteratorom?",
    ],
)
def test_programming_words_are_not_admin_terms(question) -> None:
    assert not answer.ADMIN_TERMS.search(question)


@pytest.mark.parametrize(
    "raw",
    [
        "U priloženim izvorima ne nalazi se opći opis korištenja Stream API-a [1].",
        "U izvorima koje sam pronašao ne nalazi se opis toga.\n\nIzvori [1] i [2] spominju…",
        "Materijali ne pokrivaju tu temu [1].",
    ],
)
def test_prose_refusal_is_forwarded(raw: str) -> None:
    assert is_prose_refusal(raw)
    assert check_citations(raw, [Source(1, "a.pdf", [1], "t", 0.9)]) == (None, [])


@pytest.mark.parametrize(
    "raw",
    [
        "Konstruktor ne sadrži povratni tip [1].",
        "Java ne dopušta višestruko nasljeđivanje klasa [1]. Ostatak materijali ne pokrivaju.",
        "Iznimka se baca naredbom throw [1].\n\nIzvori ne objašnjavaju finally blok.",
    ],
)
def test_normal_answer_is_not_a_refusal(raw: str) -> None:
    assert not is_prose_refusal(raw)


def test_parse_verdicts() -> None:
    reply = "ODGOVARA: DA\n**PROTURJEČI**: NE\nIZVAN - da"
    assert parse_verdicts(reply, ["ODGOVARA", "PROTURJEČI", "IZVAN"]) == {
        "ODGOVARA": True,
        "PROTURJEČI": False,
        "IZVAN": True,
    }
    assert parse_verdicts("ne znam", ["ODGOVARA"]) == {"ODGOVARA": None}


@pytest.mark.parametrize(
    ("verdicts", "fails"),
    [
        ({"ODGOVARA": True, "PROTURJEČI": False, "IZVAN": True}, False),  # IZVAN alone: no
        ({"ODGOVARA": False, "PROTURJEČI": False, "IZVAN": True}, False),  # recorded only
        ({"ODGOVARA": True, "PROTURJEČI": True, "IZVAN": False}, True),
        ({"ODGOVARA": None, "PROTURJEČI": None, "IZVAN": None}, False),  # unreadable: let through
    ],
)
def test_check_fails(verdicts: dict, fails: bool) -> None:
    assert check_fails(verdicts) is fails


H22 = (
    "Stream API se u kontekstu prikazanih izvora spominje kao način za dohvat sadržaja ZIP "
    "datoteke metodom `stream()`, ali primjeri poput `.filter(...).map(...)` nisu opisani u "
    "materijalima [1][2][3][4][5]."
)
GOOD_WITH_NOTE = (
    "Downcast je moguć samo ako objekt stvarno jest podklasa [1]. Inače nastaje "
    "ClassCastException [1]. Provjera se radi operatorom instanceof [2]. "
    "Materijali ne pokrivaju detalje o vašem konkretnom kodu."
)
NO = {"ODGOVARA": False, "PROTURJEČI": False, "IZVAN": True}


def test_mostly_refusal_forwards_when_the_check_says_it_does_not_answer() -> None:
    assert is_mostly_refusal(H22)
    assert check_fails(NO, H22)
    assert not check_fails({**NO, "ODGOVARA": True}, H22)  # the check alone decides nothing
    assert not check_fails({**NO, "ODGOVARA": None}, H22)


def test_answer_with_closing_note_is_not_mostly_refusal() -> None:
    assert not is_mostly_refusal(GOOD_WITH_NOTE)
    assert not check_fails(NO, GOOD_WITH_NOTE)  # h03/h06/h13 had ODGOVARA=NE like this


def _pipeline(monkeypatch: pytest.MonkeyPatch, scope: str, answer_reply: str) -> answer.Answer:
    """answer_question with stubbed DB and models: scope label, then the answer model's reply."""
    monkeypatch.setattr(answer, "_subject", lambda kb: ("Predmet", ["Tema"]))
    monkeypatch.setattr(answer, "search", lambda *a, **kw: [_hit(1)])  # score 1.0, over the floor
    replies = {"fast": scope, "answer": answer_reply}
    monkeypatch.setattr(answer, "complete", lambda role, messages: replies[role])
    monkeypatch.setattr(answer, "answer_check", lambda q, t, c: {"ODGOVARA": True})
    return answer.answer_question(uuid.uuid4(), "C++ i Java-a u čemu se razlikuju?")


def test_drugo_alone_does_not_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _pipeline(monkeypatch, "DRUGO", "Java nema pokazivače [1].")
    assert result.scope == "DRUGO" and result.forwarded is None
    assert result.text == "Java nema pokazivače [1]."


def test_drugo_names_the_reason_of_a_later_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _pipeline(monkeypatch, "DRUGO", DONT_KNOW)
    assert result.forwarded is ForwardReason.out_of_scope
    assert _pipeline(monkeypatch, "GRADIVO", DONT_KNOW).forwarded is ForwardReason.no_source


def test_admin_label_still_forwards_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _pipeline(monkeypatch, "ADMIN", "Java nema pokazivače [1].")
    assert result.forwarded is ForwardReason.admin and result.raw is None


def test_refusal_with_navode_and_opisuju_is_mostly_refusal() -> None:
    t25 = (
        "Materijali pokrivaju definiciju kompozicije i agregacije [2]. Međutim, izvori ne navode "
        "točne razlike između ova dva pojma.\n\nTakođer, materijali ne opisuju što će se dogoditi "
        "s objektom B ako se obriše objekt A [2]."
    )
    assert is_mostly_refusal(t25) and check_fails(NO, t25)
