"""Answer evaluation on a subject's question set: does the clone answer, cite and forward right?

    uv run python -m scripts.evaluate_answers --subject <id> [--version N] [-k 5] [--rerank 20]
        [--set PATH] [--ids e01,h20] [--difficulty hard] [--limit N] [--judge]

answerable -> answered (not forwarded); cited_expected (the answer cites an expected source).
out_of_scope / admin / not_in_materials -> forwarded (and with which reason).
--judge -> the `judge` role checks each answer against the slides it cites: grounded (everything
it claims is in them) and on_topic (it answers the question). No reference answers are used.
Each question runs the `fast` and `answer` roles (~80 s on a CPU laptop with dev models; --judge
adds 10-45 s): a 50-question run took 78 min. Forwarded questions are NOT put in the teacher queue.
Report: eval/reports/<subject>/answers-v<N>[-<set>][-rerankM]-k<K>-<timestamp>.json
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from core.answer import Answer, answer_question
from core.db import app_engine
from core.kb import find_version
from core.settings import REPO_ROOT
from llm.client import complete, trace_metadata
from llm.prompts import prompt, prompt_version
from scripts.evaluate import GoldenSet, Question


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--rerank", type=int, metavar="N")
    ap.add_argument("--set", type=Path, help="set file (default eval/sets/<subject>.yaml)")
    ap.add_argument("--ids", help="comma-separated question ids")
    ap.add_argument("--difficulty", choices=["easy", "hard", "exam", "check"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--judge", action="store_true", help="grade answers with the `judge` role")
    ap.add_argument("--no-check", action="store_true", help="skip the live answer check")
    args = ap.parse_args()

    default_path = REPO_ROOT / "eval" / "sets" / f"{args.subject}.yaml"
    path = (args.set or default_path).resolve()
    golden = GoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if golden.subject_id != args.subject:
        raise SystemExit(f"{path.name} is for subject {golden.subject_id}, not {args.subject}")
    questions = golden.questions
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q.id in wanted]
    if args.difficulty:
        questions = [q for q in questions if q.difficulty == args.difficulty]
    questions = questions[: args.limit]
    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)

    started = time.time()
    results = []
    errors: dict[str, str] = {}
    for i, q in enumerate(questions, 1):
        try:  # one failed model call (e.g. a crashed runner) must not lose the whole run
            a = retry(
                lambda q=q: answer_question(
                    version.id,
                    q.question,
                    k=args.k,
                    rerank_candidates=args.rerank,
                    check=not args.no_check,
                )
            )
        except Exception as e:  # noqa: BLE001 - recorded in the report, run continues
            errors[q.id] = repr(e)[:300]
            print(f"[{i}/{len(questions)}] {q.id} ERROR {errors[q.id]}", flush=True)
            continue
        r = score(q, a)
        if args.judge and not a.forwarded:
            try:
                r["judge"] = retry(lambda a=a: judge(a))
            except Exception as e:  # noqa: BLE001
                r["judge_error"] = repr(e)[:300]
        results.append(r)
        verdict = r.get("judge", {})
        print(
            f"[{i}/{len(questions)}] {q.id} {q.type:12} "
            f"{'forwarded:' + a.forwarded.value if a.forwarded else 'answered'}"
            f"{'' if a.forwarded else ' cites ' + ','.join(map(str, r['cited_pages']))}"
            f"{' expected-hit' if r.get('cited_expected') else ''}"
            f"{' ' + json.dumps(verdict) if verdict else ''}  ({sum(a.seconds.values()):.0f}s)",
            flush=True,
        )

    summary = summarise(results)
    report = {
        "subject": args.subject,
        "kb_version": version.version,
        "kb_version_id": str(version.id),
        "k": args.k,
        "rerank_candidates": args.rerank,
        "models": {"embedding": trace_metadata("embedding")}
        | ({"reranker": trace_metadata("reranker")} if args.rerank else {})
        | ({"judge": trace_metadata("judge")} if args.judge else {}),
        "judge_prompt_version": prompt_version(["judge"], "judge") if args.judge else None,
        "golden_set": str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path),
        "summary": summary,
        "errors": errors,
        "seconds": round(time.time() - started, 1),
        "results": results,
        "created_at": datetime.now(UTC).isoformat(),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    set_tag = (
        ""
        if path == default_path.resolve()
        else "-" + path.stem.removeprefix(args.subject).strip("._-")
    )
    mode = set_tag + (f"-rerank{args.rerank}" if args.rerank else "")
    out = REPO_ROOT / "eval" / "reports" / args.subject
    out = out / f"answers-v{version.version}{mode}-k{args.k}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"\n{args.subject} v{version.version}, k={args.k}, rerank={args.rerank}, "
        f"{len(results)} questions"
    )
    for name, values in summary.items():
        print(f"  {name:10} {values}")
    if errors:
        print(f"  errors ({len(errors)}): {list(errors)}")
    print(f"  {report['seconds']:.0f}s")
    print(f"report: {out}")


def retry(call, attempts: int = 2, wait: float = 15.0):
    """Ollama restarts a killed model runner on the next request, so one retry is enough."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)


def score(q: Question, a: Answer) -> dict:
    expected = {(s.document, s.page_or_slide) for s in q.expected_sources}
    cited_pages = sorted({p for s in a.cited for p in s.pages})
    seen_pages = sorted({p for s in a.sources for p in s.pages})
    r = {
        "id": q.id,
        "type": q.type,
        "difficulty": q.difficulty,
        "question": q.question,
        "expected_pages": sorted(p for _, p in expected),
        "forwarded": a.forwarded.value if a.forwarded else None,
        "scope": a.scope,
        "check": a.check,
        "answer": a.text,
        "raw": a.raw,
        "cited_pages": cited_pages,
        "seen_pages": seen_pages,
        "seconds": a.seconds,
        "trace": a.trace,
    }
    if q.type == "answerable":
        cited = {(s.file_name, p) for s in a.cited for p in s.pages}
        seen = {(s.file_name, p) for s in a.sources for p in s.pages}
        r["cited_expected"] = bool(cited & expected)
        r["retrieved_expected"] = bool(seen & expected)  # the answer had a chance
    return r


def judge(a: Answer) -> dict:
    """grounded / on_topic from the `judge` role, judged against the cited sources only."""
    reply = complete(
        "judge",
        [
            {
                "role": "user",
                "content": prompt("judge", "judge").format(
                    sources="\n\n".join(f"[{s.n}] ({s.citation})\n{s.text}" for s in a.cited),
                    question=a.question,
                    answer=a.text,
                ),
            }
        ],
    )

    def verdict(label: str) -> bool | None:
        m = re.search(rf"{label}\s*:\s*(DA|NE)", reply.upper())
        return None if not m else m.group(1) == "DA"

    return {"grounded": verdict("UTEMELJENO"), "on_topic": verdict("ODGOVARA")}


def summarise(results: list[dict]) -> dict:
    summary: dict = {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r["difficulty"] if r["type"] == "answerable" else "abstain"].append(r)
    for name, rs in groups.items():
        n = len(rs)
        if name == "abstain":
            reasons: dict[str, int] = defaultdict(int)
            for r in rs:
                reasons[r["forwarded"] or "answered"] += 1
            summary[name] = {
                "n": n,
                "forwarded": round(sum(r["forwarded"] is not None for r in rs) / n, 3),
                "outcomes": dict(reasons),
            }
            continue
        answered = [r for r in rs if not r["forwarded"]]
        s = {
            "n": n,
            "answered": round(len(answered) / n, 3),
            "cited_expected": round(sum(r["cited_expected"] for r in rs) / n, 3),
            "retrieved_expected": round(sum(r["retrieved_expected"] for r in rs) / n, 3),
        }
        judged = [r["judge"] for r in answered if "judge" in r]
        for key in ("grounded", "on_topic"):
            if judged:
                s[key] = round(sum(bool(j[key]) for j in judged) / len(judged), 3)
        forwards: dict[str, int] = defaultdict(int)
        for r in rs:
            if r["forwarded"]:
                forwards[r["forwarded"]] += 1
        if forwards:
            s["wrongly_forwarded"] = dict(forwards)
        summary[name] = s
    return summary


if __name__ == "__main__":
    main()
