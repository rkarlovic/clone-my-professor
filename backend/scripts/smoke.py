"""End-to-end wiring check for the retrieval spike: models.yaml -> LiteLLM -> Ollama embedding
-> pgvector (under RLS) -> cosine search. Creates a throwaway KB and removes it afterwards.

    uv run python -m scripts.smoke
"""

import sys
import uuid

from sqlalchemy import create_engine, select, text

from core.db import kb_scope
from core.models import Chunk, Document
from core.settings import get_settings
from llm.client import embed, trace_metadata

PASSAGES = [
    "Derivacija funkcije opisuje brzinu promjene funkcije u točki.",
    "Integral je površina ispod krivulje funkcije na zadanom intervalu.",
    "Fotosinteza je proces kojim biljke pretvaraju svjetlost u kemijsku energiju.",
]
QUERY = "Što je derivacija?"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Croatian text on Windows consoles (cp1252)
    owner = create_engine(get_settings().database_owner_url)
    teacher, kb, version = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with owner.begin() as c:
        c.execute(text("INSERT INTO teacher (id, name) VALUES (:t, 'smoke')"), {"t": teacher})
        c.execute(
            text("INSERT INTO knowledge_base (id, teacher_id, subject_name) VALUES (:k, :t, 'x')"),
            {"k": kb, "t": teacher},
        )
        c.execute(
            text("INSERT INTO kb_version (id, kb_id, version) VALUES (:v, :k, 1)"),
            {"v": version, "k": kb},
        )
    try:
        print("embedding via", trace_metadata("embedding"))
        vectors = embed(PASSAGES + [QUERY])
        with kb_scope(version) as s:
            doc = Document(kb_version_id=version, file_name="smoke.txt", file_hash="0" * 64)
            s.add(doc)
            s.flush()
            for i, (passage, vec) in enumerate(zip(PASSAGES, vectors, strict=False)):
                s.add(
                    Chunk(
                        kb_version_id=version,
                        document_id=doc.id,
                        text=passage,
                        page_or_slide=i + 1,
                        embedding=vec,
                    )
                )
        with kb_scope(version) as s:
            distance = Chunk.embedding.cosine_distance(vectors[-1])
            rows = s.execute(
                select(Chunk.text, Chunk.page_or_slide, distance).order_by(distance).limit(3)
            ).all()
        print(f"query: {QUERY}")
        for chunk_text, page, dist in rows:
            print(f"  {1 - dist:.3f}  [p.{page}]  {chunk_text}")
        assert rows[0].page_or_slide == 1, "expected the derivative passage to rank first"
        print("OK")
    finally:
        with owner.begin() as c:
            c.execute(text("DELETE FROM chunk WHERE kb_version_id = :v"), {"v": version})
            c.execute(text("DELETE FROM document WHERE kb_version_id = :v"), {"v": version})
            c.execute(text("DELETE FROM kb_version WHERE id = :v"), {"v": version})
            c.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb})
            c.execute(text("DELETE FROM teacher WHERE id = :t"), {"t": teacher})


if __name__ == "__main__":
    main()
