"""Show what retrieval finds for a question, or (--answer) the clone's answer.

uv run python -m scripts.ask --subject <folder> [--version N] [-k 5] [--rerank 20]
                             [--full] [--answer [--no-queue]] "pitanje"

--answer: scope check, retrieval, answer with citations (`fast` + `answer` roles). A question the
clone forwards goes to the teacher's queue (teacher_question) unless --no-queue.
"""

import argparse
import json
import sys

from sqlalchemy.orm import Session

from core.answer import answer_question
from core.db import app_engine
from core.kb import find_version
from core.queue import forward_to_teacher
from retrieval.search import search


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int, help="default: newest version")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--rerank", type=int, metavar="N", help="rerank the top N dense hits")
    ap.add_argument("--full", action="store_true", help="print whole chunks")
    ap.add_argument("--answer", action="store_true", help="generate the clone's answer")
    ap.add_argument("--no-queue", action="store_true", help="do not queue forwarded questions")
    args = ap.parse_args()

    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)
    print(f"{args.subject} v{version.version} ({version.status.value})\n")
    if args.answer:
        show_answer(args, version.id)
        return
    for i, hit in enumerate(
        search(version.id, args.question, args.k, rerank_candidates=args.rerank),
        1,
    ):
        body = hit.text if args.full else hit.text[:300].replace("\n", " ")
        print(f"{i}. {hit.score:.3f}  [{hit.citation}]  {hit.content_type}")
        print(f"   {hit.heading_path}")
        print(f"   {body}\n")


def show_answer(args: argparse.Namespace, kb_version_id) -> None:
    a = answer_question(
        kb_version_id, args.question, k=args.k, rerank_candidates=args.rerank, source="cli"
    )
    print("AI asistent (odgovara iz materijala kolegija):\n")  # rule 6: always disclosed
    print(a.text + "\n")
    if a.cited:
        print("Izvori:")
        for src in a.cited:
            print(f"  [{src.n}] {src.citation}")
    if a.forwarded:
        print(f"forwarded: {a.forwarded.value} (scope check: {a.scope})")
        if a.raw:
            print(f"model reply: {a.raw[:500]}")
        if not args.no_queue:
            with Session(app_engine()) as s, s.begin():
                item = forward_to_teacher(s, kb_version_id, args.question, a.forwarded)
                print(f"queued for the teacher: teacher_question {item.id}")
    print(f"\nscope: {a.scope}, seconds: {a.seconds}")
    print("sources seen: " + "; ".join(f"[{s.n}] {s.citation} ({s.score:.3f})" for s in a.sources))
    print("trace: " + json.dumps(a.trace, ensure_ascii=False))


if __name__ == "__main__":
    main()
