"""Add BGE-M3 sparse vectors (`sparse` role, experiment) to an existing kb_version.

    uv run python -m scripts.sparse --subject <folder> [--version N] [--batch 8]

Computed from exactly the text that was dense-embedded (heading path + context + chunk), so
hybrid search compares like with like. Fills `chunk.sparse_embedding`; dense vectors and the
rest of the version are untouched. Then: scripts.evaluate --hybrid.
"""

import argparse
import logging
import sys
import time

from pgvector import SparseVector
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine, kb_scope
from core.kb import find_version
from core.models import SPARSE_DIM, Chunk
from ingestion.pipeline import embedded_text
from llm.client import trace_metadata
from llm.sparse import sparse_embed

log = logging.getLogger("scripts.sparse")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int, help="default: newest version")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)
    with kb_scope(version.id) as s:
        rows = s.execute(
            select(Chunk.id, Chunk.heading_path, Chunk.context, Chunk.text).where(
                Chunk.embedding.is_not(None)
            )
        ).all()
    log.info(
        "v%d: %d chunks; %s",
        version.version,
        len(rows),
        trace_metadata("sparse") | {"kb_version_id": str(version.id)},
    )

    started = time.time()
    vectors: dict = {}
    for i in range(0, len(rows), args.batch):
        part = rows[i : i + args.batch]
        texts = [embedded_text(h, c, t) for _, h, c, t in part]
        vectors.update(
            zip((r[0] for r in part), sparse_embed(texts, batch=args.batch), strict=True)
        )
        if (i // args.batch) % 10 == 0:
            log.info("sparse %d/%d", min(i + args.batch, len(rows)), len(rows))

    with kb_scope(version.id) as s:
        for chunk_id, vec in vectors.items():
            s.get(Chunk, chunk_id).sparse_embedding = SparseVector(vec, SPARSE_DIM)
    mean_terms = sum(len(v) for v in vectors.values()) / max(len(vectors), 1)
    print(
        f"{args.subject} v{version.version}: {len(vectors)} sparse vectors, "
        f"{mean_terms:.0f} terms on average, {time.time() - started:.0f}s"
    )


if __name__ == "__main__":
    main()
