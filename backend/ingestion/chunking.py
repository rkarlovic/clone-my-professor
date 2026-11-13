"""Structure-aware chunking (CLAUDE.md "Chunking recipe").

Two stages, kept separate so the chunking rules are testable without Docling:
  1. `slides_from_docling` turns a parsed document into one `Page` per page/slide.
  2. `build_chunks` applies the recipe: drop repeated boilerplate, fold duplicate pages into
     their first occurrence, merge tiny fragments, group "Title (2)" continuations under one
     parent, split over-long pages with overlap.
"""

import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from docling_core.types.doc import DocItemLabel, DoclingDocument

from core.models import ContentType
from ingestion.parse import OutlineEntry

TokenCounter = Callable[[str], int]

_PRIVATE_USE = re.compile("[-]")  # bullet glyphs from symbol fonts
_IMAGE_MARKER = re.compile(r"^\s*<!-- image -->\s*$", re.MULTILINE)
_CONTINUATION = re.compile(r"\s*\((?:\d+|\d+\s*/\s*\d+)\)\s*$")  # "Title (2)", "Title (1/3)"
_LECTURE_NUMBER = re.compile(r"^(?P<num>\d+[a-z]?)[_\s]+(?:\d{4}\s+)?(?P<rest>.*)$")
_TRAILING_PAREN = re.compile(r"\s*-?\s*\([^()]*\)\s*$")


@dataclass
class Page:
    page: int  # 1-based page or slide number in the source file
    title: str
    body: str  # Markdown, title line removed
    section: str  # e.g. lecture name from the PDF outline; "" if unknown
    images: int = 0  # picture count, for a later `vision` captioning pass
    also_on: list[int] = field(default_factory=list)  # pages repeating this one (duplicates)


@dataclass
class ChunkDraft:
    """A chunk before it gets a kb_version/document id. Parents have embed=False."""

    key: uuid.UUID
    text: str
    heading_path: str
    page: int
    content_type: ContentType
    section: str
    parent_key: uuid.UUID | None = None
    embed: bool = True
    pages: list[int] = field(default_factory=list)
    also_on_pages: list[int] = field(default_factory=list)  # same content repeated there


@dataclass(frozen=True)
class ChunkSettings:
    min_tokens: int = 25  # pages with less body than this merge into a neighbour
    max_tokens: int = 800  # longer child chunks are split by paragraph
    overlap: float = 0.12  # only for those splits
    boilerplate_repeats: int = 3  # identical pages seen this often are dropped
    # A page whose words (any order, diacritics ignored) overlap an earlier page with the same
    # title by at least this much, with identical numbers, is folded into it. None: keep all.
    # 0.85 tolerates an OCR slip; a real edit (e.g. `private` -> package access) stays apart.
    duplicate_similarity: float | None = 0.85


# ---------------------------------------------------------------- stage 1: Docling -> pages


def _title_parts(title: str) -> tuple[str, list[str]]:
    """'07_2016 Subject - Iznimke - (Inst, Author).pdf (p.1-9)' -> ('07', ['Subject', 'Iznimke'])"""
    t = re.sub(r"\.(pdf|pptx?|docx?)\b.*$", "", title, flags=re.IGNORECASE).strip()
    num = ""
    if m := _LECTURE_NUMBER.match(t):
        num, t = m["num"], m["rest"]
    t = _TRAILING_PAREN.sub("", t)  # author/institution, glued on or after " - "
    parts = [p.strip(" -") for p in re.split(r"\s+-\s+", t)]
    return num, [p for p in parts if p]


def section_names(titles: list[str]) -> list[str]:
    """Short lecture labels for a set of bookmark titles.

    Bookmark names follow the teacher's own, not always consistent, convention. Instead of
    one exact pattern: strip extension/page range, the leading 'NN_YYYY' and a trailing
    '(Institution, Author)', then drop parts that repeat in most titles (the subject name).
    Markers such as '(notes)' are kept so duplicate lectures stay distinguishable."""
    split = [_title_parts(t) for t in titles]
    counts = Counter(p for _, parts in split for p in set(parts))
    common = {p for p, n in counts.items() if len(titles) >= 3 and n > len(titles) / 2}
    names = []
    for num, parts in split:
        kept = [p for p in parts if p not in common] or parts
        label = ""
        for p in kept:  # "(notes)" attaches to the previous part, others join with a dash
            sep = " " if p.startswith("(") else " – "
            label = f"{label}{sep}{p}" if label else p
        names.append(f"{num} {label}".strip())
    return names


def section_name(title: str) -> str:
    return section_names([title])[0]


def section_index(outline: list[OutlineEntry]) -> list[tuple[int, str]]:
    """(first page, label) for each top-level bookmark, in page order."""
    top = sorted((e for e in outline if e.level == 0), key=lambda e: e.first_page)
    return list(
        zip((e.first_page for e in top), section_names([e.title for e in top]), strict=True)
    )


def _section_for_page(index: list[tuple[int, str]], page: int) -> str:
    current = ""
    for first_page, label in index:
        if first_page > page:
            break
        current = label
    return current


def clean_text(text: str) -> str:
    text = _PRIVATE_USE.sub("", text)
    text = _IMAGE_MARKER.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"^(\s*[-*])\s+", r"\1 ", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_HEADINGS = (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER)
_SKIP = (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE)

# (page_no, item) -> original text with line breaks, or None. Docling joins code lines into one
# line; the PDF text layer still has them.
CodeTextSource = Callable[[int, object], str | None]


def _render_item(doc: DoclingDocument, item, code_text: CodeTextSource | None) -> str:
    label = item.label
    if label in _HEADINGS:
        return f"## {item.text}"
    if label == DocItemLabel.LIST_ITEM:
        return f"- {item.text}"
    if label == DocItemLabel.CODE:
        original = code_text(item.prov[0].page_no, item) if code_text and item.prov else None
        return f"```\n{(original or item.text).strip()}\n```"
    if label == DocItemLabel.TABLE:
        return item.export_to_markdown(doc=doc)
    return getattr(item, "text", "") or ""


def slides_from_docling(
    doc: DoclingDocument, outline: list[OutlineEntry], code_text: CodeTextSource | None = None
) -> list[Page]:
    """One Page per page/slide, built in a single pass over the document (Docling's
    per-page Markdown export re-walks the whole document for every page)."""
    by_page: dict[int, list] = {p: [] for p in range(1, doc.num_pages() + 1)}
    for item, _ in doc.iterate_items():
        if getattr(item, "prov", None):
            by_page.setdefault(item.prov[0].page_no, []).append(item)

    sections = section_index(outline)
    pages: list[Page] = []
    for page_no, items in sorted(by_page.items()):
        title_item = next(
            (i for i in items if i.label in _HEADINGS and getattr(i, "text", "").strip()), None
        )
        body, prev_label = "", None
        for i in items:
            if i is title_item or i.label in _SKIP:
                continue
            block = _render_item(doc, i, code_text).strip()
            if not block:
                continue
            tight = prev_label == i.label == DocItemLabel.LIST_ITEM  # keep lists compact
            body += ("\n" if tight else "\n\n") + block if body else block
            prev_label = i.label
        pages.append(
            Page(
                page=page_no,
                title=clean_text(title_item.text) if title_item else "",
                body=clean_text(body),
                section=_section_for_page(sections, page_no),
                images=sum(i.label == DocItemLabel.PICTURE for i in items),
            )
        )
    return pages


# ---------------------------------------------------------------- stage 2: pages -> chunks


def base_title(title: str) -> str:
    return _CONTINUATION.sub("", title).strip()


def classify(title: str, body: str) -> ContentType:
    """Cheap heuristic for the spike; later the `fast` role or the teacher sets this."""
    t = title.lower()
    # Only solutions to homework/exercises are withheld (rule 4). A solved lecture example
    # ("Rješenje primjera 2") or a concept ("Moguća rješenja zastoja") is teaching content.
    if re.match(r"r(j|ij)e[sš]enj\w*\s+(\S+\s+)?(zadat|zada[cć]|doma[cć]|vje[zž]b)", t):
        return ContentType.solution
    if re.match(r"(domaća zadaća|domaca zadaca|zadatak|zadaci|vježba|vjezba)", t):
        return ContentType.exercise
    if re.match(r"primjer", t) or re.search(r"r(j|ij)e[sš]enj\w*\s+primjer", t):
        return ContentType.example
    if re.match(r"definicij", t) or re.match(r"definicij", body.lower()):
        return ContentType.definition
    return ContentType.other


def _normalise(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def drop_boilerplate(pages: list[Page], repeats: int) -> list[Page]:
    """Remove pages whose text occurs `repeats`+ times (licence slides, 'Pitanja?' ...)."""
    counts = Counter(_normalise(p.title + " " + p.body) for p in pages)
    return [
        p for p in pages if (key := _normalise(p.title + " " + p.body)) and counts[key] < repeats
    ]


_MIN_DUPLICATE_WORDS = 8  # shorter pages (dividers, "Pitanja?") are too thin to compare
_TITLE_SIMILARITY = 0.9  # tolerates OCR slips in a title ("Koristen]e" vs "Korištenje")


def _fold(text: str) -> str:
    """Lowercase without diacritics: a copy of a slide may be typed without č/ć/š/ž/đ."""
    text = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if not unicodedata.combining(c))


@dataclass
class _Signature:
    title: str
    title_numbers: list[str]
    words: set[str]
    numbers: set[str]

    @classmethod
    def of(cls, p: Page) -> "_Signature":
        title = " ".join(re.findall(r"\w+", _fold(p.title)))
        words = set(re.findall(r"\w+", _fold(p.body)))
        return cls(title, re.findall(r"\d+", title), words, {w for w in words if w.isdigit()})

    def repeats(self, other: "_Signature", min_similarity: float) -> bool:
        # Numbers must match exactly: they are what tells lecture 10's title slide from
        # lecture 12's, "Primjer (2)" from "(3)", or homework 7 from homework 10.
        if self.numbers != other.numbers:
            return False
        overlap = len(self.words & other.words) / len(self.words | other.words)
        if overlap < min_similarity:
            return False
        if not (self.title and other.title):  # a title the parser missed on one copy
            return True
        return (
            self.title_numbers == other.title_numbers
            and SequenceMatcher(None, self.title, other.title).ratio() >= _TITLE_SIMILARITY
        )


def fold_duplicates(pages: list[Page], min_similarity: float | None) -> list[Page]:
    """Drop pages that repeat an earlier page (a lecture included twice, a slide reused in a
    later lecture) and record their numbers on the first occurrence, so it is indexed once
    but can be cited everywhere it appears. Order of items and diacritics are ignored."""
    if min_similarity is None:
        return pages
    kept: list[tuple[Page, _Signature]] = []
    for p in pages:
        p = replace(p, also_on=list(p.also_on))  # the caller's pages stay unchanged
        sig = _Signature.of(p)
        if len(sig.words) >= _MIN_DUPLICATE_WORDS:
            first = next((k for k, ks in kept if sig.repeats(ks, min_similarity)), None)
            if first is not None:
                first.also_on.append(p.page)
                continue
        kept.append((p, sig))
    return [p for p, _ in kept]


def _render(title: str, body: str) -> str:
    return f"## {title}\n\n{body}".strip() if title else body


@dataclass
class _Unit:
    title: str
    text: str
    pages: list[int]
    section: str
    main_page: int  # the page with the most body: what a citation should point to
    main_tokens: int
    also_on: list[int] = field(default_factory=list)

    def absorb(self, other: "_Unit", before: bool) -> None:
        self.text = f"{other.text}\n\n{self.text}" if before else f"{self.text}\n\n{other.text}"
        self.pages = sorted(self.pages + other.pages)
        self.also_on = sorted(self.also_on + other.also_on)
        if other.main_tokens > self.main_tokens:
            self.main_page, self.main_tokens = other.main_page, other.main_tokens


def merge_tiny(pages: list[Page], count: TokenCounter, min_tokens: int) -> list[_Unit]:
    """A page with almost no body (section divider, image-only slide) joins the next page of
    the same section, or the previous one if it is the last."""
    units: list[_Unit] = []
    carry: _Unit | None = None

    def flush_backwards(c: _Unit) -> None:
        if units and units[-1].section == c.section:
            units[-1].absorb(c, before=False)
        else:  # a section consisting only of tiny pages stays as it is
            units.append(c)

    for p in pages:
        tokens = count(p.body)
        unit = _Unit(
            p.title, _render(p.title, p.body), [p.page], p.section, p.page, tokens, list(p.also_on)
        )
        if carry and carry.section == unit.section:
            unit.absorb(carry, before=True)
        elif carry:
            flush_backwards(carry)
        carry = None
        if tokens < min_tokens:
            carry = unit
        else:
            units.append(unit)
    if carry:
        flush_backwards(carry)
    return units


def split_long(text: str, count: TokenCounter, max_tokens: int, overlap: float) -> list[str]:
    """Split by blank-line paragraphs into pieces <= max_tokens, repeating the tail of the
    previous piece (~overlap). Code fences are never split."""
    if count(text) <= max_tokens:
        return [text]
    blocks, buf, in_code = [], [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code and buf:
            blocks.append("\n".join(buf))
            buf = []
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf))

    pieces: list[str] = []
    current: list[str] = []
    for block in blocks:
        if current and count("\n\n".join(current + [block])) > max_tokens:
            pieces.append("\n\n".join(current))
            tail: list[str] = []
            for prev in reversed(current):
                if count("\n\n".join([prev] + tail)) > max_tokens * overlap:
                    break
                tail.insert(0, prev)
            current = tail
        current.append(block)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def build_chunks(
    pages: list[Page], subject: str, count: TokenCounter, settings: ChunkSettings | None = None
) -> list[ChunkDraft]:
    settings = settings or ChunkSettings()
    pages = drop_boilerplate(pages, settings.boilerplate_repeats)
    pages = fold_duplicates(pages, settings.duplicate_similarity)
    units = merge_tiny(pages, count, settings.min_tokens)

    # Group consecutive units of one section that share a base title ("X", "X (2)", "X (3)").
    groups: list[list[_Unit]] = []
    for u in units:
        prev = groups[-1][-1] if groups else None
        if (
            prev
            and prev.section == u.section
            and base_title(u.title)
            and base_title(prev.title) == base_title(u.title)
        ):
            groups[-1].append(u)
        else:
            groups.append([u])

    chunks: list[ChunkDraft] = []
    for group in groups:
        first = group[0]
        title = base_title(first.title)
        heading_path = " › ".join(x for x in (subject, first.section, title) if x)
        ctype = classify(title, "\n".join(u.text for u in group))
        parent_key = None
        if len(group) > 1:
            parent_key = uuid.uuid4()
            chunks.append(
                ChunkDraft(
                    key=parent_key,
                    text="\n\n".join(u.text for u in group),
                    heading_path=heading_path,
                    page=first.main_page,
                    content_type=ctype,
                    section=first.section,
                    embed=False,
                    pages=[p for u in group for p in u.pages],
                    also_on_pages=sorted(p for u in group for p in u.also_on),
                )
            )
        for u in group:
            for piece in split_long(u.text, count, settings.max_tokens, settings.overlap):
                chunks.append(
                    ChunkDraft(
                        key=uuid.uuid4(),
                        text=piece,
                        heading_path=heading_path,
                        page=u.main_page,
                        content_type=ctype,
                        section=u.section,
                        parent_key=parent_key,
                        pages=list(u.pages),
                        also_on_pages=list(u.also_on),
                    )
                )
    return chunks
