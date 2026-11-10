"""Structure-aware chunking (CLAUDE.md "Chunking recipe").

Two stages, kept separate so the chunking rules are testable without Docling:
  1. `slides_from_docling` turns a parsed document into one `Page` per page/slide.
  2. `build_chunks` applies the recipe: drop repeated boilerplate, merge tiny fragments,
     group "Title (2)" continuations under one parent, split over-long pages with overlap.
"""

import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from docling_core.types.doc import DocItemLabel, DoclingDocument

from core.models import ContentType
from ingestion.parse import OutlineEntry

TokenCounter = Callable[[str], int]

_PRIVATE_USE = re.compile("[-]")  # bullet glyphs from symbol fonts
_IMAGE_MARKER = re.compile(r"^\s*<!-- image -->\s*$", re.MULTILINE)
_CONTINUATION = re.compile(r"\s*\((?:\d+|\d+\s*/\s*\d+)\)\s*$")  # "Title (2)", "Title (1/3)"
_LECTURE_TITLE = re.compile(r"^(?P<num>\d+[a-z]?)_\d{4}\s.*?\s-\s(?P<topic>.+?)\s-\s\(")


@dataclass
class Page:
    page: int  # 1-based page or slide number in the source file
    title: str
    body: str  # Markdown, title line removed
    section: str  # e.g. lecture name from the PDF outline; "" if unknown
    images: int = 0  # picture count, for a later `vision` captioning pass


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


@dataclass(frozen=True)
class ChunkSettings:
    min_tokens: int = 25  # pages with less body than this merge into a neighbour
    max_tokens: int = 800  # longer child chunks are split by paragraph
    overlap: float = 0.12  # only for those splits
    boilerplate_repeats: int = 3  # identical pages seen this often are dropped


# ---------------------------------------------------------------- stage 1: Docling -> pages


def section_name(outline_title: str) -> str:
    """'07_2016 Subject - Iznimke - (UNIPU, X).pdf (p.422-480)' -> '07 Iznimke'."""
    m = _LECTURE_TITLE.match(outline_title)
    if m:
        return f"{m['num']} {m['topic'].strip()}"
    return re.sub(r"\.pdf.*$", "", outline_title).strip()


def _section_for_page(outline: list[OutlineEntry], page: int) -> str:
    top = [e for e in outline if e.level == 0 and e.first_page <= page]
    return section_name(top[-1].title) if top else ""


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
                section=_section_for_page(outline, page_no),
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

    def absorb(self, other: "_Unit", before: bool) -> None:
        self.text = f"{other.text}\n\n{self.text}" if before else f"{self.text}\n\n{other.text}"
        self.pages = sorted(self.pages + other.pages)
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
        unit = _Unit(p.title, _render(p.title, p.body), [p.page], p.section, p.page, tokens)
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
                    )
                )
    return chunks
