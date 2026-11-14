"""Dense search inside one kb_version (RLS-enforced), optional rerank, parent expansion."""

import uuid
from dataclasses import dataclass, field

from pgvector import SparseVector
from sqlalchemy import select, text
from sqlalchemy.orm import aliased

from core.db import kb_scope
from core.models import SPARSE_DIM, Chunk, ContentType, Document
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
    situating: str | None = None  # the `context` role's sentence, if the version has one

    @property
    def rerank_text(self) -> str:
        """What the reranker reads: the same parts as the embedded text (heading path, context
        sentence, chunk), so a context ingest can change reranking as well as search."""
        return "\n".join(x for x in (self.heading_path, self.situating, self.text) if x)

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


RRF_K = 60  # standard reciprocal-rank-fusion constant
HYBRID_CANDIDATES = 20  # hits taken from each of dense and sparse before fusing


def search(
    kb_version_id: uuid.UUID,
    query: str,
    k: int = 5,
    *,
    include_solutions: bool = False,
    rerank_candidates: int | None = None,
    hybrid: bool = False,
) -> list[Hit]:
    """Top-k chunks for `query`.

    hybrid: dense and sparse (BGE-M3 lexical weights, `sparse` role) each return their best
    candidates, fused by reciprocal rank; the score is then the fused score.
    rerank_candidates: that many hits (dense or fused) are re-scored by the `reranker` role and
    the best k kept."""
    limit = max(k, rerank_candidates or 0, HYBRID_CANDIDATES if hybrid else 0)
    (query_vec,) = embed([query])
    dense_order = Chunk.embedding.cosine_distance(query_vec)
    with kb_scope(kb_version_id) as s:
        # Keep scanning the HNSW index when the version filter (RLS) removes candidates.
        s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        hits = _ranked(s, Chunk.embedding, dense_order, limit, include_solutions, flip=True)
        if hybrid:
            from llm.sparse import sparse_embed  # experiment; loads the model in-process

            (q,) = sparse_embed([query])
            sparse_order = Chunk.sparse_embedding.max_inner_product(SparseVector(q, SPARSE_DIM))
            lexical = _ranked(s, Chunk.sparse_embedding, sparse_order, limit, include_solutions)
            if not lexical:
                raise RuntimeError("this version has no sparse vectors (run scripts.sparse)")
            hits = fuse([hits, lexical])[:limit]
    if rerank_candidates and hits:
        order = rerank(query, [h.rerank_text for h in hits], top_n=k)
        hits = [_with_score(hits[i], score) for i, score in order]
    return hits[:k]


def _ranked(s, column, order, limit: int, include_solutions: bool, flip: bool = False) -> list[Hit]:
    """Best `limit` chunks by `order` (a pgvector distance: smaller is better). Scores are
    1 - distance for cosine (`flip`) and the inner product for sparse."""
    parent = aliased(Chunk)
    stmt = (
        select(Chunk, Document.file_name, parent.text, order.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .outerjoin(parent, parent.id == Chunk.parent_id)
        .where(column.is_not(None))
        .order_by(order)
        .limit(limit)
    )
    if not include_solutions:  # rule 4: no full solutions by default
        stmt = stmt.where(Chunk.content_type != ContentType.solution)
    hits = [  # build plain objects while the session is open
        Hit(
            chunk_id=c.id,
            score=(1.0 - dist) if flip else -dist,
            text=c.text,
            context_text=parent_text or c.text,
            heading_path=c.heading_path or "",
            file_name=file_name,
            page=c.page_or_slide,
            content_type=c.content_type.value,
            also_on=list(c.also_on_pages or []),
            situating=c.context,
        )
        for c, file_name, parent_text, dist in s.execute(stmt).all()
    ]
    hits.sort(key=lambda h: h.score, reverse=True)  # relaxed_order may be slightly out of order
    return hits


def fuse(rankings: list[list[Hit]]) -> list[Hit]:
    """Reciprocal rank fusion: a chunk scores sum(1 / (RRF_K + rank)) over the lists it is in,
    so agreement between dense and sparse wins without comparing their raw scores."""
    best: dict[uuid.UUID, Hit] = {}
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            best.setdefault(hit.chunk_id, hit)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    order = sorted(scores, key=scores.get, reverse=True)
    return [_with_score(best[i], round(scores[i], 6)) for i in order]


def _with_score(hit: Hit, score: float) -> Hit:
    hit.score = score
    return hit
