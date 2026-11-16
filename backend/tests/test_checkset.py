"""Auto-generated check set: slide spread, reply cleaning, set format (no model, no DB)."""

import random
import uuid

import pytest

import core.checkset as checkset
from core.checkset import GENERIC_ABSTAIN, SKIP, Slide, clean_question, spread, strip_diacritics
from llm.prompts import PROMPTS
from scripts.evaluate import GoldenSet


def test_spread_covers_the_whole_material() -> None:
    bins = spread(list(range(100)), 10, random.Random(0))
    assert len(bins) == 10
    assert [sorted(b) for b in bins] == [list(range(i, i + 10)) for i in range(0, 100, 10)]
    assert len(spread(list(range(3)), 10, random.Random(0))) == 3  # fewer slides than questions


@pytest.mark.parametrize(
    ("reply", "question"),
    [
        ("Zašto mi se prozor zamrzne?", "Zašto mi se prozor zamrzne?"),
        ('Pitanje: "Kako radi equals?"', "Kako radi equals?"),
        ("„Što je enkapsulacija?”\n\nOvo pitanje…", "Što je enkapsulacija?"),
        (SKIP, None),
        ("preskoči", None),
        ("", None),
        ("Ok.", None),
    ],
)
def test_clean_question(reply: str, question: str | None) -> None:
    assert clean_question(reply) == question


def test_strip_diacritics() -> None:
    assert strip_diacritics("Što je nasljeđivanje? Čemu služi žica?") == (
        "Sto je nasljedivanje? Cemu sluzi zica?"
    )


def test_prompt_formats() -> None:
    text = PROMPTS["checkset_question"].format(subject="s", heading_path="h", text="t", style="x")
    assert SKIP in text


def test_generated_set_is_a_valid_golden_set(monkeypatch: pytest.MonkeyPatch) -> None:
    slides = [Slide("a.pdf", p, "Predmet › Tema", "riječ " * 40) for p in range(1, 21)]
    monkeypatch.setattr(checkset, "eligible_slides", lambda kb: slides)
    replies = iter([SKIP] + ["Čemu služi ovo što radim?"] * 50)  # first pick skipped
    monkeypatch.setattr(checkset, "complete", lambda role, messages: next(replies))

    questions = checkset.generate(uuid.uuid4(), "Predmet", 5, seed=1)
    slide_qs = [q for q in questions if q["type"] == "answerable"]
    assert len(slide_qs) == 5  # the skipped slide was replaced by another from its bin
    assert len(questions) == 5 + len(GENERIC_ABSTAIN)
    pages = [q["expected_sources"][0]["page_or_slide"] for q in slide_qs]
    assert [(p - 1) // 4 for p in pages] == [0, 1, 2, 3, 4]  # one per fifth of the material
    styles = [q["notes"] for q in slide_qs]
    assert styles[4] == "style: no_diacritics" and slide_qs[4]["question"] == (
        "cemu sluzi ovo sto radim?"
    )
    GoldenSet.model_validate({"subject_id": "s", "questions": questions})
