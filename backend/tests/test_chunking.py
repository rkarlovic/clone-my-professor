from core.models import ContentType
from ingestion.chunking import (
    ChunkSettings,
    Page,
    base_title,
    build_chunks,
    classify,
    clean_text,
    section_name,
    section_names,
    split_long,
)


def words(text: str) -> int:  # stand-in tokenizer: one token per word
    return len(text.split())


def page(n: int, title: str, body: str, section: str = "01 Java") -> Page:
    return Page(page=n, title=title, body=body, section=section)


BODY = " ".join(["riječ"] * 40)


# Bookmark titles shaped like a real merged lecture PDF, including its inconsistencies:
# a typo in the institution, a part glued to the author parenthesis, notes/slides twins.
BOOKMARKS = [
    "00_2016 Osnove programiranja - Uvodno predavanje - (UNI, Autor).pdf (p.1-17)",
    "02a_2016 Osnove programiranja - Zasto OOP - (notes) - (UNI, Autor).pdf (p.18-40)",
    "02a_2016 Osnove programiranja - Zasto OOP - (slides) - (UNI, Autor).pdf (p.41-60)",
    "04_2016 Osnove programiranja - Nasljedjivanje. Polimorfizam. - (UNNI, Autor).pdf (p.61-80)",
    "07_2016 Osnove programiranja - Iznimke - (UNI, Autor).pdf (p.81-99)",
    "11b_2016 Osnove programiranja - DODATAK - Git i GitHub + Projekti (UNI, Autor).pdf (p.100-9)",
]


def test_section_names_from_merged_pdf_bookmarks() -> None:
    assert section_names(BOOKMARKS) == [
        "00 Uvodno predavanje",
        "02a Zasto OOP (notes)",
        "02a Zasto OOP (slides)",
        "04 Nasljedjivanje. Polimorfizam.",
        "07 Iznimke",
        "11b DODATAK – Git i GitHub + Projekti",
    ]


def test_section_name_without_convention_falls_back_to_cleaned_title() -> None:
    assert section_name("Poglavlje 3.pdf") == "Poglavlje 3"
    assert section_name("Uvod u Javu") == "Uvod u Javu"


def test_base_title_strips_continuation_markers() -> None:
    assert base_title("Primjer 7.1: Hvatanje iznimke (2)") == "Primjer 7.1: Hvatanje iznimke"
    assert base_title("Literatura (1/3)") == "Literatura"
    assert base_title("Java 8") == "Java 8"


def test_clean_text_removes_symbol_bullets_and_image_markers() -> None:
    assert clean_text("-  metoda\n<!-- image -->\n\n\n\nkraj") == "- metoda\n\nkraj"


def test_classify() -> None:
    assert classify("Domaća zadaća 7.", "") is ContentType.exercise
    assert classify("Rješenje zadatka 2", "") is ContentType.solution
    assert classify("Rješenja domaće zadaće 3", "") is ContentType.solution
    assert classify("Rješenje primjera 2 b)", "") is ContentType.example
    assert classify("Moguća rješenja zastoja", "") is ContentType.other
    assert classify("Težište - rješenje korištenjem polja", "") is ContentType.other
    assert classify("Primjer 7.2: UML dijagram", "") is ContentType.example
    assert classify("Iznimke", "Definicija: iznimka je ...") is ContentType.definition
    assert classify("Hijerarhija iznimki", "") is ContentType.other


def test_one_chunk_per_slide_with_heading_path_and_page() -> None:
    chunks = build_chunks([page(5, "Klase", BODY), page(6, "Objekti", BODY)], "STP", words)
    assert [(c.page, c.heading_path) for c in chunks] == [
        (5, "STP › 01 Java › Klase"),
        (6, "STP › 01 Java › Objekti"),
    ]
    assert all(c.embed and c.parent_key is None for c in chunks)
    assert chunks[0].text.startswith("## Klase\n\n")


def test_repeated_boilerplate_pages_are_dropped() -> None:
    licence = [page(i, "Creative Commons", "slobodno možete dijeliti", f"0{i}") for i in range(3)]
    chunks = build_chunks(licence + [page(9, "Klase", BODY)], "STP", words)
    assert [c.page for c in chunks] == [9]


def test_tiny_slide_merges_into_next_slide_of_same_section() -> None:
    pages = [page(1, "Vlastita klasa za iznimke", ""), page(2, "Primjer 7.2", BODY)]
    (chunk,) = build_chunks(pages, "STP", words)
    assert chunk.pages == [1, 2]
    assert chunk.page == 2  # cite the slide with the content, not the divider before it
    assert "Vlastita klasa za iznimke" in chunk.text and BODY in chunk.text


def test_tiny_last_slide_merges_backwards_not_across_sections() -> None:
    pages = [page(1, "Klase", BODY), page(2, "Pitanja?", ""), page(3, "Uvod", BODY, "02 OOP")]
    chunks = build_chunks(pages, "STP", words)
    assert [c.pages for c in chunks] == [[1, 2], [3]]


def test_continuation_slides_share_a_parent() -> None:
    pages = [page(1, "Literatura", BODY), page(2, "Literatura (2)", BODY), page(3, "Kraj", BODY)]
    chunks = build_chunks(pages, "STP", words)
    parents = [c for c in chunks if not c.embed]
    children = [c for c in chunks if c.embed]
    assert len(parents) == 1 and parents[0].pages == [1, 2]
    assert [c.parent_key for c in children] == [parents[0].key, parents[0].key, None]
    assert children[1].heading_path.endswith("› Literatura")


def test_split_long_respects_limit_overlaps_and_keeps_code_whole() -> None:
    paragraphs = [" ".join([f"p{i}"] * 30) for i in range(6)]
    code = "```\n" + "\n\n".join(["x = 1;"] * 5) + "\n```"
    text = "\n\n".join(paragraphs[:3] + [code] + paragraphs[3:])
    pieces = split_long(text, words, max_tokens=70, overlap=0.5)
    assert len(pieces) > 1
    assert all(words(p) <= 70 for p in pieces if "```" not in p)
    assert sum(code in p for p in pieces) >= 1  # code fence never cut in half
    assert pieces[1].startswith(pieces[0].split("\n\n")[-1])  # overlap carries the tail


def test_settings_are_respected() -> None:
    chunks = build_chunks(
        [page(1, "A", "kratko"), page(2, "B", BODY)], "S", words, ChunkSettings(min_tokens=1)
    )
    assert [c.pages for c in chunks] == [[1], [2]]
