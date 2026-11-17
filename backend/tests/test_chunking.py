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


COHESION = (
    "- Koliko usko su operacije unutar postupka povezane\n"
    "- Vrlo važna kada razmatramo KAKO napraviti postupak\n"
    "- Funkcija Cosine() ima dobru koheziju jer je cijeli postupak posvećen jednom zadatku\n"
    "- Funkcija CosineAndTan() ima lošiju koheziju jer pokušava raditi više od jednog zadatka"
)
# The same slide in a lecture's "notes" copy: items reordered, typed without diacritics.
COHESION_NOTES = (
    "- Vrlo vazna kada razmatramo KAKO napraviti postupak\n"
    "- Koliko usko su operacije unutar postupka povezane\n"
    "- Funkcija Cosine () ima dobru koheziju jer je cijeli postupak posvecen jednom zadatku\n"
    "- Funkcija CosineAndTan () ima losiju koheziju jer pokusava raditi vise od jednog zadatka"
)


def test_duplicate_pages_fold_into_the_first_and_remember_where_they_repeat() -> None:
    pages = [
        page(87, "Smjernica #6: Kohezija", COHESION_NOTES, "02a OOP (notes)"),
        page(100, "Klase", BODY),
        page(119, "Smjernica #6: Kohezija", COHESION, "02a OOP (slides)"),
        page(303, "Smjernica #6: Kohezija", COHESION, "05 Sučelja"),
    ]
    chunks = build_chunks(pages, "STP", words)
    assert [(c.page, c.also_on_pages) for c in chunks] == [(87, [119, 303]), (100, [])]


def test_pages_that_differ_in_numbers_are_not_duplicates() -> None:
    # Title slides of two lectures, build-up slides of one example, two homeworks: same title,
    # nearly the same words, but different content.
    lecture = (
        "Sveučilište u Puli, diplomski studij, {n}. Grafičko sučelje Swing, ak. g. 2016./2017."
    )
    pages = [
        page(623, "Suvremene tehnike programiranja", lecture.format(n=10)),
        page(766, "Suvremene tehnike programiranja", lecture.format(n=12)),
        page(148, "Primjer 1.4: Spajanje stringova (2)", BODY),
        page(149, "Primjer 1.4: Spajanje stringova (3)", BODY),
        page(480, "Domaća zadaća 7", COHESION, "07 Iznimke"),
        page(693, "Domaća zadaća 10", COHESION, "10 Swing"),
    ]
    chunks = build_chunks(pages, "STP", words, ChunkSettings(min_tokens=1))
    assert all(c.also_on_pages == [] for c in chunks)
    assert {c.page for c in chunks if c.embed} == {623, 766, 148, 149, 480, 693}


def test_a_real_edit_is_not_folded_but_an_ocr_slip_or_missing_title_is() -> None:
    code = (
        "Primjer apstraktne klase: atribut {} String name, metoda getName vraća ime, set postavlja"
    )
    ocr_slip = COHESION.replace("posvećen", "pornocen")
    pages = [
        page(310, "Primjer 5.3", code.format("private")),
        page(334, "Primjer 5.3", code.format("paketno vidljiv")),  # what the example teaches
        page(86, "Smjernica #6: Kohezija", COHESION),
        page(118, "", COHESION),  # the parser found no title on this copy
        page(94, "Modeliranje", COHESION + " modeliranje"),
        page(126, "Modeliranje", ocr_slip + " modeliranje"),
    ]
    chunks = build_chunks(pages, "STP", words, ChunkSettings(min_tokens=1))
    folded = {c.page: c.also_on_pages for c in chunks if c.embed}
    assert folded == {310: [], 334: [], 86: [118], 94: [126]}


def test_duplicate_folding_can_be_switched_off_and_leaves_input_pages_unchanged() -> None:
    pages = [page(1, "Kohezija", COHESION), page(2, "Kohezija", COHESION)]
    off = build_chunks(pages, "STP", words, ChunkSettings(duplicate_similarity=None))
    assert [c.page for c in off if c.embed] == [1, 2]
    build_chunks(pages, "STP", words)
    (chunk,) = build_chunks(pages, "STP", words)  # a second run does not pile up pages
    assert chunk.also_on_pages == [2] and pages[0].also_on == []


def test_settings_are_respected() -> None:
    chunks = build_chunks(
        [page(1, "A", "kratko"), page(2, "B", BODY)], "S", words, ChunkSettings(min_tokens=1)
    )
    assert [c.pages for c in chunks] == [[1], [2]]


def test_clean_text_drops_empty_bullet_lines() -> None:
    assert clean_text("Tekst\n\n-\n\n- -\n– •\nkraj") == "Tekst\n\nkraj"
    assert clean_text("| a | b |\n|---|---|") == "| a | b |\n|---|---|"  # tables stay


def test_split_long_splits_one_long_paragraph_by_sentences() -> None:
    text = " ".join(f"Rečenica broj {i} ima pet." for i in range(40))  # 200 words, no breaks
    pieces = split_long(text, words, max_tokens=50, overlap=0.1)
    assert len(pieces) > 3 and all(words(p) <= 50 for p in pieces)
    assert all(p.startswith("Rečenica") for p in pieces)  # cut at sentence ends


def test_split_long_falls_back_to_word_windows_without_sentence_ends() -> None:
    text = " ".join(f"pojam{i} - opis pojma" for i in range(100))  # a flattened glossary
    pieces = split_long(text, words, max_tokens=60, overlap=0.1)
    assert all(words(p) <= 60 for p in pieces)
    assert " ".join(pieces).split() == text.split()  # nothing lost, no overlap needed
