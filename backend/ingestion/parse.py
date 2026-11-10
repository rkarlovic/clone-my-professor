"""Parse source files with Docling. Parsing is slow (seconds per page on CPU), so the result is
cached as Docling JSON next to the material; re-chunking with other settings reuses it."""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from docling_core.types.doc import DoclingDocument

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".docx"}


@dataclass(frozen=True)
class OutlineEntry:
    """A bookmark in the source PDF (e.g. one lecture inside a merged PDF)."""

    title: str
    level: int
    first_page: int  # 1-based


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cache_dir_for(source_dir: Path) -> Path:
    """data/<subject>/  ->  data/.cache/<subject>/  (git-ignored with the rest of data/)."""
    return source_dir.parent / ".cache" / source_dir.name


def parse_file(path: Path, cache_dir: Path, *, ocr: bool = False) -> DoclingDocument:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{file_sha256(path)[:16]}-ocr{int(ocr)}.docling.json"
    if cached.exists():
        log.info("parse cache hit: %s", cached.name)
        return DoclingDocument.load_from_json(cached)

    # Imported lazily: loading Docling's models takes several seconds.
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # OCR only helps for scans; on born-digital slides it costs ~3x time for no extra text.
    options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    log.info("parsing %s (ocr=%s) ...", path.name, ocr)
    doc = converter.convert(path).document
    doc.save_as_json(cached)
    return doc


class PdfTextLayer:
    """Reads the PDF's own text inside a Docling item's box. Used for code, where Docling
    joins all lines into one but the text layer keeps the line breaks."""

    def __init__(self, path: Path, doc: DoclingDocument) -> None:
        import pypdfium2 as pdfium

        self._pdf = pdfium.PdfDocument(path)
        self._doc = doc
        self._textpages: dict[int, object] = {}

    def __call__(self, page_no: int, item) -> str | None:
        try:
            height = self._doc.pages[page_no].size.height
            box = item.prov[0].bbox.to_bottom_left_origin(height)
            if page_no not in self._textpages:
                self._textpages[page_no] = self._pdf[page_no - 1].get_textpage()
            pad = 2.0
            text = self._textpages[page_no].get_text_bounded(
                left=box.l - pad, bottom=box.b - pad, right=box.r + pad, top=box.t + pad
            )
        except Exception:  # text layer missing or odd geometry: fall back to Docling's text
            log.debug("no text layer for code on page %d", page_no, exc_info=True)
            return None
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return text or None

    def close(self) -> None:
        for tp in self._textpages.values():
            tp.close()
        self._pdf.close()


def pdf_outline(path: Path) -> list[OutlineEntry]:
    """Bookmarks of a PDF; empty for other formats or PDFs without an outline."""
    if path.suffix.lower() != ".pdf":
        return []
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        entries = []
        for item in pdf.get_toc():
            dest = item.get_dest()
            index = dest.get_index() if dest is not None else None
            if index is not None:
                entries.append(OutlineEntry(item.get_title().strip(), item.level, index + 1))
        return entries
    finally:
        pdf.close()
