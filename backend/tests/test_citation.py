import uuid

from retrieval.search import Hit


def hit(page: int | None, also_on: list[int]) -> Hit:
    return Hit(uuid.uuid4(), 0.9, "t", "t", "S › A", "stp.pdf", page, "other", also_on)


def test_citation_names_every_page_a_folded_duplicate_is_on() -> None:
    assert hit(41, [291, 303]).citation == "stp.pdf, str. 41 (isto i na str. 291, 303)"
    assert hit(41, [291, 303]).pages == [41, 291, 303]


def test_citation_without_duplicates_or_page() -> None:
    assert hit(7, []).citation == "stp.pdf, str. 7"
    assert hit(None, []).citation == "stp.pdf" and hit(None, []).pages == []
