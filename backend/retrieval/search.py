"""Dense search inside one kb_version (RLS-enforced), optional rerank, parent expansion."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import aliased

from core.db import kb_scope
from core.models import Chunk, ContentType, Document
from llm.client import embed, rerank


@dataclass
class Hit:
    chunk_id: uuid.UUID
    score: float  # cosine similarity, or reranker relevance when reranked
    text: str  # the matched chunk (clean original text)
    context_text: str  # what the LLM gets: the parent section if there is one
    heading_path: str
    file_name: str
    page: int | None
    content_type: str
    also_on: list[int] = field(default_factory=list)  # same content on these pages too

    @property
    def pages(self) -> list[int]:
        """Every page this content is on; the first is where it is cited."""
        return ([self.page] if self.page else []) + self.also_on

    @property
    def citation(self) -> str:
        if not self.page:
            return self.file_name
        also = f" (isto i na str. {', '.join(map(str, self.also_on))})" if self.also_on else ""
        return f"{self.file_name}, str. {self.page}{also}"


def search(
    kb_version_id: uuid.UUID,
    query: str,
    k: int = 5,
    *,
    include_solutions: bool = False,
    rerank_candidates: int | None = None,
) -> list[Hit]:
    """Top-k chunks for `query`. With `rerank_candidates`, that many dense hits are re-scored
    by the `reranker` role and the best k kept."""
    (query_vec,) = embed([query])
    limit = max(k, rerank_candidates or 0)
    parent = aliased(Chunk)
    distance = Chunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(Chunk, Document.file_name, parent.text, distance.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .outerjoin(parent, parent.id == Chunk.parent_id)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    if not include_solutions:  # rule 4: no full solutions by default
        stmt = stmt.where(Chunk.content_type != ContentType.solution)

    with kb_scope(kb_version_id) as s:
        # Keep scanning the HNSW index when the version filter (RLS) removes candidates.
        s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        hits = [  # build plain objects while the session is open
            Hit(
                chunk_id=c.id,
                score=1.0 - dist,
                text=c.text,
                context_text=parent_text or c.text,
                heading_path=c.heading_path or "",
                file_name=file_name,
                page=c.page_or_slide,
                content_type=c.content_type.value,
                also_on=list(c.also_on_pages or []),
            )
            for c, file_name, parent_text, dist in s.execute(stmt).all()
        ]
    hits.sort(key=lambda h: h.score, reverse=True)  # relaxed_order may be slightly out of order
    if rerank_candidates and hits:
        order = rerank(query, [f"{h.heading_path}\n{h.text}" for h in hits], top_n=k)
        hits = [_with_score(hits[i], score) for i, score in order]
    return hits[:k]


def _with_score(hit: Hit, score: float) -> Hit:
    hit.score = score
    return hit
