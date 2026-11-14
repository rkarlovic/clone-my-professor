import uuid

from retrieval.search import RRF_K, Hit, fuse


def hit(page: int | None, also_on: list[int]) -> Hit:
    return Hit(uuid.uuid4(), 0.9, "t", "t", "S › A", "stp.pdf", page, "other", also_on)


def test_rank_fusion_rewards_agreement_between_dense_and_sparse() -> None:
    a, b, c, d = (hit(n, []) for n in (1, 2, 3, 4))
    fused = fuse([[a, b, c], [d, b, a]])  # dense, sparse
    assert [h.page for h in fused] == [1, 2, 4, 3]  # a and b are in both lists, c and d once
    assert fused[0].score == round(1 / (RRF_K + 1) + 1 / (RRF_K + 3), 6)


def test_citation_names_every_page_a_folded_duplicate_is_on() -> None:
    assert hit(41, [291, 303]).citation == "stp.pdf, str. 41 (isto i na str. 291, 303)"
    assert hit(41, [291, 303]).pages == [41, 291, 303]


def test_reranker_reads_heading_context_and_text_like_the_embedding() -> None:
    h = hit(7, [])
    assert h.rerank_text == "S › A\nt"  # no context sentence: unchanged from before
    h.situating = "Isječak o sučeljima."
    assert h.rerank_text == "S › A\nIsječak o sučeljima.\nt"


def test_citation_without_duplicates_or_page() -> None:
    assert hit(7, []).citation == "stp.pdf, str. 7"
    assert hit(None, []).citation == "stp.pdf" and hit(None, []).pages == []
