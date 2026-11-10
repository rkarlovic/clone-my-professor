"""Ingest one subject folder into a new draft kb_version:
parse (cached) -> pages -> chunks -> [context] -> embeddings -> pgvector (under RLS)."""

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from core.db import app_engine, kb_scope
from core.kb import create_draft_version, get_or_create_kb
from core.models import Chunk, ContentType, Document
from ingestion.chunking import ChunkDraft, ChunkSettings, Page, build_chunks, slides_from_docling
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
    seconds: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def embedding_text(draft: ChunkDraft, context: str | None) -> str:
    """What gets embedded: heading path + optional context + text (recipe step 4)."""
    return "\n".join(x for x in (draft.heading_path, context, "", draft.text) if x is not None)


SOLUTION_FOLDERS = {"rjesenja", "rješenja", "rjesenje", "rješenje", "solutions"}


def is_solutions_file(path: Path, source_dir: Path) -> bool:
    """Files the teacher keeps in a solutions folder are tagged `solution` as a whole."""
    return any(part.lower() in SOLUTION_FOLDERS for part in path.relative_to(source_dir).parts)


def load_pages(path: Path, doc) -> list[Page]:
    outline = pdf_outline(path)
    if path.suffix.lower() != ".pdf":
        return slides_from_docling(doc, outline)
    text_layer = PdfTextLayer(path, doc)
    try:
        return slides_from_docling(doc, outline, code_text=text_layer)
    finally:
        text_layer.close()


def source_files(source_dir: Path) -> list[Path]:
    return sorted(p for p in source_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)


def ingest_subject(source_dir: Path, options: IngestOptions | None = None) -> IngestReport:
    options = options or IngestOptions()
    subject = source_dir.name
    started = time.time()
    count_tokens = embedding_token_counter()

    with Session(app_engine()) as s, s.begin():
        kb = get_or_create_kb(s, subject)
        version = create_draft_version(s, kb.id)
        version_id, version_no = version.id, version.version

    models = {"embedding": trace_metadata("embedding")}
    if options.with_context:
        models["context"] = trace_metadata("context")
    report = IngestReport(subject, str(version_id), version_no, asdict(options), models)
    log.info("subject %s -> kb_version %s (v%d)", subject, version_id, version_no)

    for path in source_files(source_dir):
        doc = parse_file(path, cache_dir_for(source_dir), ocr=options.ocr)
        pages = load_pages(path, doc)
        drafts = build_chunks(pages, subject.replace("_", " "), count_tokens, options.chunking)
        if is_solutions_file(path, source_dir):  # rule 4: excluded from retrieval by default
            for d in drafts:
                d.content_type = ContentType.solution
        to_embed = [d for d in drafts if d.embed]

        contexts: dict = {}
        if options.with_context:
            from ingestion.context import chunk_context

            for i, d in enumerate(to_embed, 1):
                contexts[d.key] = chunk_context(d.heading_path, d.text)
                if i % 50 == 0:
                    log.info("context %d/%d", i, len(to_embed))

        vectors: dict = {}
        for i in range(0, len(to_embed), options.embed_batch):
            batch = to_embed[i : i + options.embed_batch]
            texts = [embedding_text(d, contexts.get(d.key)) for d in batch]
            vectors.update(zip((d.key for d in batch), embed(texts), strict=True))
            log.info("embedded %d/%d", min(i + options.embed_batch, len(to_embed)), len(to_embed))

        with kb_scope(version_id) as s:
            document = Document(
                kb_version_id=version_id,
                source_platform="upload",
                file_name=str(path.relative_to(source_dir)),
                file_hash=file_sha256(path),
                modified_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
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
                            week_topic=d.section or None,
                            embedding=vectors.get(d.key),
                        )
                    )
                s.flush()

        report.documents += 1
        report.pages += len(pages)
        report.chunks_embedded += len(to_embed)
        report.parent_chunks += len(drafts) - len(to_embed)
        for d in to_embed:
            report.content_types[d.content_type.value] = (
                report.content_types.get(d.content_type.value, 0) + 1
            )

    report.seconds = round(time.time() - started, 1)
    return report
