"""Ingestion into a draft kb_version:
parse (cached) -> pages -> chunks -> [context] -> embeddings -> pgvector (under RLS).

Two entry points share `ingest_document`:
- `ingest_subject`: a folder (data/<subject>/) into a new draft version (scripts.ingest);
- `process_version`: an uploaded draft version (background job, jobs/tasks.py): carries
  unchanged documents over from its base version and processes the uploaded files.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine, kb_scope
from core.kb import create_draft_version, get_or_create_kb
from core.models import Chunk, ContentType, Document, IngestStatus, KbVersion, KnowledgeBase
from core.settings import get_settings
from ingestion.chunking import (
    ChunkDraft,
    ChunkSettings,
    Page,
    build_chunks,
    section_name,
    slides_from_docling,
)
from ingestion.parse import (
    SUPPORTED_SUFFIXES,
    PdfTextLayer,
    cache_dir_for,
    file_sha256,
    parse_file,
    pdf_outline,
)
from llm.client import embed, trace_metadata
from llm.tokens import embedding_token_counter

log = logging.getLogger(__name__)


@dataclass
class IngestOptions:
    chunking: ChunkSettings = field(default_factory=ChunkSettings)
    ocr: bool = False
    with_context: bool = False  # ask the `context` role for a situating sentence per chunk
    embed_batch: int = 32


@dataclass
class IngestReport:
    subject: str
    kb_version_id: str
    version: int
    options: dict
    models: dict
    documents: int = 0
    pages: int = 0
    chunks_embedded: int = 0
    parent_chunks: int = 0
    content_types: dict = field(default_factory=dict)
    # Every section label with its chunk count: review it after each ingest, labels come from
    # the teacher's bookmark/file names and a bad one is otherwise only seen in search results.
    sections: dict = field(default_factory=dict)
    # Folded duplicate pages, "<file> p.<n>" -> the page they are indexed under. Review it
    # too: a wrong fold hides a slide's own content behind another slide's citation.
    duplicates: dict = field(default_factory=dict)
    # Documents copied unchanged from the base version (uploads only; not re-processed).
    carried_over: list = field(default_factory=list)
    seconds: float = 0.0
    # Seconds per step (parse, context, embedding, store), summed over documents, so later
    # ingests can be planned: on a CPU laptop the context step dominates.
    timings: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def embedded_text(heading_path: str | None, context: str | None, text: str) -> str:
    """What gets embedded: heading path + optional context + text (recipe
    step 4). Stored chunks keep these parts separately, so the text can be rebuilt later."""
    return "\n".join(x for x in (heading_path, context, "", text) if x is not None)


def embedding_text(draft: ChunkDraft, context: str | None) -> str:
    return embedded_text(draft.heading_path, context, draft.text)


SOLUTION_FOLDERS = {"rjesenja", "rješenja", "rjesenje", "rješenje", "solutions"}


def is_solutions_file(path: Path, source_dir: Path) -> bool:
    """Files the teacher keeps in a solutions folder are tagged `solution` as a whole."""
    return any(part.lower() in SOLUTION_FOLDERS for part in path.relative_to(source_dir).parts)


def load_pages(path: Path, doc, label: str) -> list[Page]:
    outline = pdf_outline(path)
    if path.suffix.lower() != ".pdf":
        return slides_from_docling(doc, outline, default_section=label)
    text_layer = PdfTextLayer(path, doc)
    try:
        return slides_from_docling(doc, outline, code_text=text_layer, default_section=label)
    finally:
        text_layer.close()


def source_files(source_dir: Path) -> list[Path]:
    return sorted(p for p in source_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)


def _models(options: IngestOptions) -> dict:
    models = {"embedding": trace_metadata("embedding")}
    if options.with_context:
        from ingestion.context import prompt_version

        # The prompt shapes the context as much as the model does: record both.
        models["context"] = trace_metadata("context") | {"prompt_version": prompt_version()}
    return models


class _Clock:
    """Adds the seconds since the last lap to report.timings[step]."""

    def __init__(self, report: IngestReport) -> None:
        self.report, self.last = report, time.time()

    def lap(self, step: str) -> None:
        now = time.time()
        t = self.report.timings
        t[step] = round(t.get(step, 0.0) + now - self.last, 1)
        self.last = now


def ingest_document(
    version_id: uuid.UUID,
    path: Path,
    *,
    file_name: str,
    subject: str,
    options: IngestOptions,
    report: IngestReport,
    cache_dir: Path,
    count_tokens: Callable[[str], int],
    solution: bool = False,
    modified_at: datetime | None = None,
) -> None:
    """Parse, chunk, embed and store one file as a document of `version_id`.
    `file_name` is the name students see in citations; `path` is where the bytes are."""
    clock = _Clock(report)
    doc = parse_file(path, cache_dir, ocr=options.ocr)
    pages = load_pages(path, doc, section_name(Path(file_name).name))
    drafts = build_chunks(pages, subject.replace("_", " "), count_tokens, options.chunking)
    if solution:  # rule 4: excluded from retrieval by default
        for d in drafts:
            d.content_type = ContentType.solution
    to_embed = [d for d in drafts if d.embed]
    clock.lap("parse_and_chunk")

    contexts: dict = {}
    if options.with_context:
        from ingestion.context import chunk_context

        for i, d in enumerate(to_embed, 1):
            contexts[d.key] = chunk_context(d.heading_path, d.text)
            if i % 50 == 0:
                log.info("context %d/%d", i, len(to_embed))
        clock.lap("context")

    vectors: dict = {}
    for i in range(0, len(to_embed), options.embed_batch):
        batch = to_embed[i : i + options.embed_batch]
        texts = [embedding_text(d, contexts.get(d.key)) for d in batch]
        vectors.update(zip((d.key for d in batch), embed(texts), strict=True))
        log.info("embedded %d/%d", min(i + options.embed_batch, len(to_embed)), len(to_embed))
    clock.lap("embedding")

    with kb_scope(version_id) as s:
        document = Document(
            kb_version_id=version_id,
            source_platform="upload",
            file_name=file_name,
            file_hash=file_sha256(path),
            modified_at=modified_at or datetime.fromtimestamp(path.stat().st_mtime, UTC),
        )
        s.add(document)
        s.flush()
        # Parents first: children reference them through parent_id.
        for parents_pass in (True, False):
            for d in drafts:
                if (not d.embed) != parents_pass:
                    continue
                s.add(
                    Chunk(
                        id=d.key,
                        kb_version_id=version_id,
                        document_id=document.id,
                        parent_id=d.parent_key,
                        text=d.text,
                        context=contexts.get(d.key),
                        heading_path=d.heading_path,
                        content_type=d.content_type,
                        page_or_slide=d.page,
                        also_on_pages=d.also_on_pages,
                        week_topic=d.section or None,
                        embedding=vectors.get(d.key),
                    )
                )
            s.flush()
    clock.lap("store")

    report.documents += 1
    report.pages += len(pages)
    report.chunks_embedded += len(to_embed)
    report.parent_chunks += len(drafts) - len(to_embed)
    for d in to_embed:
        report.content_types[d.content_type.value] = (
            report.content_types.get(d.content_type.value, 0) + 1
        )
        label = f"{file_name} › {d.section or '(no section)'}"
        report.sections[label] = report.sections.get(label, 0) + 1
        for dup in d.also_on_pages:
            report.duplicates[f"{file_name} p.{dup}"] = d.page


def ingest_subject(source_dir: Path, options: IngestOptions | None = None) -> IngestReport:
    options = options or IngestOptions()
    subject = source_dir.name
    started = time.time()
    count_tokens = embedding_token_counter()

    with Session(app_engine()) as s, s.begin():
        kb = get_or_create_kb(s, subject)
        version = create_draft_version(s, kb.id)
        version.ingest_status = IngestStatus.processing
        version_id, version_no = version.id, version.version

    report = IngestReport(subject, str(version_id), version_no, asdict(options), _models(options))
    log.info("subject %s -> kb_version %s (v%d)", subject, version_id, version_no)
    for path in source_files(source_dir):
        ingest_document(
            version_id,
            path,
            file_name=str(path.relative_to(source_dir)),
            subject=subject,
            options=options,
            report=report,
            cache_dir=cache_dir_for(source_dir),
            count_tokens=count_tokens,
            solution=is_solutions_file(path, source_dir),
        )
    report.seconds = round(time.time() - started, 1)
    _finish(version_id, report)
    return report


def copy_documents(
    from_version: uuid.UUID, to_version: uuid.UUID, file_names: set[str]
) -> list[str]:
    """Copy documents (by file name) and their chunks, embeddings included, into another
    version of the same knowledge base. Nothing is re-parsed or re-embedded."""
    with kb_scope(from_version) as s:
        docs = s.scalars(select(Document).where(Document.file_name.in_(file_names))).all()
        chunks = s.scalars(select(Chunk).where(Chunk.document_id.in_([d.id for d in docs]))).all()
        doc_rows = [
            {
                c: getattr(d, c)
                for c in (
                    "id",
                    "source_platform",
                    "external_id",
                    "file_name",
                    "file_hash",
                    "modified_at",
                )
            }
            for d in docs
        ]
        chunk_rows = [
            {
                c: getattr(ch, c)
                for c in (
                    "id",
                    "document_id",
                    "parent_id",
                    "text",
                    "context",
                    "heading_path",
                    "content_type",
                    "page_or_slide",
                    "also_on_pages",
                    "week_topic",
                    "embedding",
                )
            }
            for ch in chunks
        ]
    new_doc = {r["id"]: uuid.uuid4() for r in doc_rows}
    new_chunk = {r["id"]: uuid.uuid4() for r in chunk_rows}
    with kb_scope(to_version) as s:
        for r in doc_rows:
            s.add(Document(**(r | {"id": new_doc[r["id"]], "kb_version_id": to_version})))
        s.flush()
        # Parents (no parent_id) first, as in ingest_document.
        for parents_pass in (True, False):
            for r in chunk_rows:
                if (r["parent_id"] is None) != parents_pass:
                    continue
                s.add(
                    Chunk(
                        **r
                        | {
                            "id": new_chunk[r["id"]],
                            "kb_version_id": to_version,
                            "document_id": new_doc[r["document_id"]],
                            "parent_id": new_chunk.get(r["parent_id"]),
                        }
                    )
                )
            s.flush()
    return sorted(r["file_name"] for r in doc_rows)


class FileFailed(RuntimeError):
    """One uploaded file could not be processed; the message names the file, not the server's
    paths, because the teacher sees it."""

    def __init__(self, file_name: str, cause: Exception) -> None:
        kind = type(cause).__name__
        hint = (
            "the file could not be read (damaged, password-protected or not really this type)"
            if "Conversion" in kind
            else f"processing failed ({kind})"
        )
        super().__init__(f"{file_name}: {hint}")


def _internal_error(exc: Exception) -> str:
    return f"processing failed ({type(exc).__name__}); the details are in the worker log"


def process_version(
    version_id: uuid.UUID, options: IngestOptions | None = None
) -> IngestReport | None:
    """The ingest job for an uploaded draft version: copy unchanged documents from the base
    version, process `pending_files`, record the report. On an error the version is marked
    `failed` with the message (the job can be run again: it starts from a clean version)."""
    options = options or IngestOptions()
    started = time.time()
    with Session(app_engine()) as s, s.begin():
        version = s.get(KbVersion, version_id, with_for_update=True)
        if version is None:
            raise LookupError(f"no kb_version {version_id}")
        if version.ingest_status == IngestStatus.ready:
            log.info("kb_version %s is already processed", version_id)
            return IngestReport(**version.ingest_report) if version.ingest_report else None
        kb = s.get(KnowledgeBase, version.kb_id)
        subject, version_no, kb_id = kb.subject_name, version.version, kb.id
        base_id, pending = version.base_version_id, list(version.pending_files)
        version.ingest_status, version.ingest_error = IngestStatus.processing, None

    report = IngestReport(subject, str(version_id), version_no, asdict(options), _models(options))
    try:
        _clear_version(version_id)  # a retry starts clean
        replaced = {f["file_name"] for f in pending}
        if base_id:
            with kb_scope(base_id) as s:
                names = set(s.scalars(select(Document.file_name)).all())
            report.carried_over = copy_documents(base_id, version_id, names - replaced)
        count_tokens = embedding_token_counter()
        cache_dir = get_settings().upload_dir / str(kb_id) / ".cache"
        for f in pending:
            log.info("v%d: processing %s", version_no, f["file_name"])
            try:
                ingest_document(
                    version_id,
                    Path(f["path"]),
                    file_name=f["file_name"],
                    subject=subject,
                    options=options,
                    report=report,
                    cache_dir=cache_dir,
                    count_tokens=count_tokens,
                    modified_at=datetime.now(UTC),
                )
            except Exception as exc:
                raise FileFailed(f["file_name"], exc) from exc
    except Exception as exc:
        # The full error (with server paths) goes to the worker log; the teacher sees the file
        # and the kind of problem.
        log.exception("kb_version %s failed", version_id)
        with Session(app_engine()) as s, s.begin():
            v = s.get(KbVersion, version_id)
            v.ingest_status = IngestStatus.failed
            v.ingest_error = str(exc) if isinstance(exc, FileFailed) else _internal_error(exc)
        raise
    report.seconds = round(time.time() - started, 1)
    _finish(version_id, report)
    return report


def _clear_version(version_id: uuid.UUID) -> None:
    with kb_scope(version_id) as s:
        s.query(Chunk).filter(Chunk.parent_id.is_not(None)).delete()
        s.query(Chunk).delete()
        s.query(Document).delete()


def _finish(version_id: uuid.UUID, report: IngestReport) -> None:
    with Session(app_engine()) as s, s.begin():
        v = s.get(KbVersion, version_id)
        v.ingest_status, v.ingest_error = IngestStatus.ready, None
        v.ingest_report = asdict(report)
