"""Show what retrieval finds for a question (no answer generation yet).

uv run python -m scripts.ask --subject <folder> [--version N] [-k 5] [--rerank 20] [--hybrid]
                             "pitanje"
"""

import argparse
import sys

from sqlalchemy.orm import Session

from core.db import app_engine
from core.kb import find_version
from retrieval.search import search


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int, help="default: newest version")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--rerank", type=int, metavar="N", help="rerank the top N dense hits")
    ap.add_argument("--hybrid", action="store_true", help="dense + sparse, rank-fused")
    ap.add_argument("--full", action="store_true", help="print whole chunks")
    args = ap.parse_args()

    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)
    print(f"{args.subject} v{version.version} ({version.status.value})\n")
    for i, hit in enumerate(
        search(
            version.id, args.question, args.k, rerank_candidates=args.rerank, hybrid=args.hybrid
        ),
        1,
    ):
        body = hit.text if args.full else hit.text[:300].replace("\n", " ")
        print(f"{i}. {hit.score:.3f}  [{hit.citation}]  {hit.content_type}")
        print(f"   {hit.heading_path}")
        print(f"   {body}\n")


if __name__ == "__main__":
    main()
