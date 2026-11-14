"""Retrieval evaluation on a subject's golden set (eval/sets/<subject>.yaml).

    uv run python -m scripts.evaluate --subject <id> [--version N] [-k 5] [--rerank 20] [--hybrid]

answerable questions -> recall@k (any expected source in the top k) and MRR, per difficulty.
out_of_scope / admin -> best retrieval score, to calibrate the abstention threshold (step 2).
Report: eval/reports/<subject>/eval-v<N>[-hybrid][-rerankM]-k<K>-<timestamp>.json
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from core.db import app_engine
from core.kb import find_version
from core.settings import REPO_ROOT
from llm.client import trace_metadata
from retrieval.search import search


class Source(BaseModel):
    document: str | None = None
    page_or_slide: int


class Question(BaseModel):
    id: str
    question: str
    type: Literal["answerable", "out_of_scope", "admin"]
    difficulty: Literal["easy", "hard"] = "easy"
    expected_sources: list[Source] = []
    reference_answer: str | None = None
    notes: str = ""


class GoldenSet(BaseModel):
    subject_id: str
    default_document: str | None = None
    questions: list[Question]

    @model_validator(mode="after")
    def check(self) -> "GoldenSet":
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate question ids")
        for q in self.questions:
            if q.type == "answerable" and not q.expected_sources:
                raise ValueError(f"{q.id}: answerable question without expected_sources")
            for s in q.expected_sources:
                s.document = s.document or self.default_document
        return self


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--rerank", type=int, metavar="N")
    ap.add_argument("--hybrid", action="store_true", help="dense + sparse, rank-fused")
    args = ap.parse_args()

    path = REPO_ROOT / "eval" / "sets" / f"{args.subject}.yaml"
    golden = GoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)

    started = time.time()
    results = []
    for q in golden.questions:
        hits = search(
            version.id, q.question, args.k, rerank_candidates=args.rerank, hybrid=args.hybrid
        )
        expected = {(src.document, src.page_or_slide) for src in q.expected_sources}
        # A folded duplicate counts on every page it is on: whichever copy the golden set
        # names, the indexed chunk is the same content.
        rank = next(
            (
                i
                for i, h in enumerate(hits, 1)
                if any((h.file_name, p) in expected for p in h.pages)
            ),
            None,
        )
        results.append(
            {
                "id": q.id,
                "type": q.type,
                "difficulty": q.difficulty,
                "question": q.question,
                "expected_pages": sorted(p for _, p in expected),
                "rank": rank,
                "top_score": round(hits[0].score, 4) if hits else None,
                "top": [
                    {
                        "page": h.page,
                        "also_on": h.also_on,
                        "score": round(h.score, 4),
                        "heading": h.heading_path,
                    }
                    for h in hits
                ],
            }
        )

    summary: dict = {}
    groups = defaultdict(list)
    for r in results:
        if r["type"] == "answerable":
            groups[r["difficulty"]].append(r)
            groups["all"].append(r)
    for name, rs in groups.items():
        summary[name] = {
            "n": len(rs),
            f"recall@{args.k}": round(sum(r["rank"] is not None for r in rs) / len(rs), 3),
            "mrr": round(sum(1 / r["rank"] for r in rs if r["rank"]) / len(rs), 3),
        }
    abstain = [r for r in results if r["type"] != "answerable"]
    answer_scores = [r["top_score"] for r in groups["all"]]
    summary["abstain"] = {
        "n": len(abstain),
        "top_scores": {r["id"]: r["top_score"] for r in abstain},
        "answerable_top_score_min": min(answer_scores) if answer_scores else None,
    }

    report = {
        "subject": args.subject,
        "kb_version": version.version,
        "kb_version_id": str(version.id),
        "k": args.k,
        "rerank_candidates": args.rerank,
        "hybrid": args.hybrid,
        "models": {"embedding": trace_metadata("embedding")}
        | ({"reranker": trace_metadata("reranker")} if args.rerank else {})
        | ({"sparse": trace_metadata("sparse")} if args.hybrid else {}),
        "golden_set": str(path.relative_to(REPO_ROOT)),
        "summary": summary,
        "seconds": round(time.time() - started, 1),
        "results": results,
        "created_at": datetime.now(UTC).isoformat(),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    mode = ("-hybrid" if args.hybrid else "") + (f"-rerank{args.rerank}" if args.rerank else "")
    name = f"eval-v{version.version}{mode}-k{args.k}-{stamp}.json"
    out = REPO_ROOT / "eval" / "reports" / args.subject / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"{args.subject} v{version.version}, k={args.k}, rerank={args.rerank}, hybrid={args.hybrid}"
    )
    for name in ("easy", "hard", "all"):
        if name in summary:
            print(f"  {name:5} {summary[name]}")
    print("  misses:")
    for r in results:
        if r["type"] == "answerable" and r["rank"] is None:
            got = ", ".join(str(t["page"]) for t in r["top"])
            print(f"    {r['id']} expected {r['expected_pages']} got [{got}]  {r['question']}")
    print(f"  abstain top scores: {summary['abstain']['top_scores']}")
    print(f"  lowest top score among answerable: {summary['abstain']['answerable_top_score_min']}")
    print(f"  {report['seconds']:.0f}s for {len(results)} questions")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
