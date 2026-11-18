"""The internal benchmark in one command (CLAUDE.md "Evaluation strategy", rule 10): for every
subject folder in data/ that has a question set, make sure it is ingested, then run retrieval
(recall@5 / MRR) and the answer evaluation (answered, cites the expected slide, forwarded,
judge) on each of its sets, and write one summary for the active models file.

    python -m scripts.benchmark [--subjects A,B] [--sets golden,teacher,check,students]
        [--retrieval-only] [--no-judge] [--limit N] [--reingest] [--context]

In the installation (the course material in the project's data/ folder, sets in eval/sets/):
    .\\setup.ps1 -Benchmark          ./setup.sh --benchmark
which runs this in the one-off `benchmark` container (infra/docker-compose.yml).

Sets of a subject: eval/sets/<subject>.yaml ("golden") and eval/sets/local/<subject>.<name>.yaml
(e.g. "teacher", "check"; git-ignored). A subject is ingested once (a new draft version, the
same as scripts.ingest) and later runs reuse its newest processed version; --reingest makes a new
one (after changing chunking or the embedding model). Each step is the existing script
(scripts.ingest / evaluate / evaluate_answers), so their reports land in eval/reports/<subject>/
as before. Summary: eval/reports/benchmark-<models version>-<time>.md and .json.
Exit code 1 if a step failed.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine
from core.models import IngestStatus, KbVersion, KnowledgeBase
from core.settings import BACKEND_DIR, REPO_ROOT
from llm.client import trace_metadata
from llm.config import get_model_config

DATA = REPO_ROOT / "data"
SETS = REPO_ROOT / "eval" / "sets"
REPORTS = REPO_ROOT / "eval" / "reports"
NOT_SUBJECTS = {"uploads", "keys", "hf"}
ROLES = ("answer", "check", "fast", "judge", "embedding")


def find_sets(subject: str) -> dict[str, Path]:
    sets = {}
    if (SETS / f"{subject}.yaml").is_file():
        sets["golden"] = SETS / f"{subject}.yaml"
    for p in sorted((SETS / "local").glob(f"{subject}.*.yaml")):
        sets[p.stem.removeprefix(subject).strip(".")] = p
    return sets


def subjects() -> list[str]:
    if not DATA.is_dir():
        return []
    return sorted(
        d.name
        for d in DATA.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in NOT_SUBJECTS
        and find_sets(d.name)
    )


def ready_version(subject: str) -> int | None:
    with Session(app_engine()) as s:
        return s.scalars(
            select(KbVersion.version)
            .join(KnowledgeBase, KnowledgeBase.id == KbVersion.kb_id)
            .where(
                KnowledgeBase.subject_name == subject,
                KbVersion.ingest_status == IngestStatus.ready,
            )
            .order_by(KbVersion.version.desc())
        ).first()


def run(module: str, *args: str) -> Path | None:
    """Run one of the scripts, show its output live, return the report it wrote (None: failed)."""
    cmd = [sys.executable, "-m", f"scripts.{module}", *args]
    print(f"\n$ {' '.join(cmd[2:])}", flush=True)
    report = None
    with subprocess.Popen(
        cmd,
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as p:
        for line in p.stdout:
            print(line, end="", flush=True)
            if m := re.match(r"report: (.+)$", line.strip()):
                report = Path(m.group(1))
    return report if p.returncode == 0 else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", help="comma-separated folders in data/ (default: all with a set)")
    ap.add_argument("--sets", help="comma-separated set names (golden, teacher, check, …)")
    ap.add_argument("--retrieval-only", action="store_true", help="skip the answer evaluation")
    ap.add_argument("--no-judge", action="store_true", help="answers without the `judge` role")
    ap.add_argument("--limit", type=int, help="first N questions of each set (answers only)")
    ap.add_argument("--reingest", action="store_true", help="new version even if one exists")
    ap.add_argument("--context", action="store_true", help="ingest with context sentences")
    args = ap.parse_args()

    cfg = get_model_config()
    chosen = args.subjects.split(",") if args.subjects else subjects()
    if not chosen:
        raise SystemExit(
            f"no subject found: put the course folders in {DATA} and their question sets in "
            f"{SETS} (see docs/GPU-SETUP.md)"
        )
    wanted_sets = set(args.sets.split(",")) if args.sets else None
    started = time.time()
    rows: list[dict] = []
    failed: list[str] = []
    print(f"benchmark · models {cfg.version} · subjects: {', '.join(chosen)}", flush=True)

    for subject in chosen:
        if not (DATA / subject).is_dir():
            failed.append(f"{subject}: no folder {DATA / subject}")
            continue
        version = None if args.reingest else ready_version(subject)
        if version is None:
            report = run("ingest", "--subject", subject, *(["--context"] if args.context else []))
            version = ready_version(subject) if report else None
            if version is None:
                failed.append(f"{subject}: ingest failed")
                continue
        for name, path in find_sets(subject).items():
            if wanted_sets and name not in wanted_sets:
                continue
            common = ["--subject", subject, "--version", str(version), "--set", str(path)]
            row: dict = {"subject": subject, "set": name, "kb_version": version}
            if r := run("evaluate", *common):
                row["retrieval"] = json.loads(r.read_text(encoding="utf-8"))["summary"]
                row["retrieval_report"] = str(r)
            else:
                failed.append(f"{subject}/{name}: retrieval failed")
            if not args.retrieval_only:
                extra = ([] if args.no_judge else ["--judge"]) + (
                    ["--limit", str(args.limit)] if args.limit else []
                )
                if r := run("evaluate_answers", *common, *extra):
                    data = json.loads(r.read_text(encoding="utf-8"))
                    row["answers"] = data["summary"]
                    row["answer_seconds"] = data["seconds"]
                    row["answer_errors"] = len(data["errors"])
                    row["answers_report"] = str(r)
                    if data["errors"]:
                        failed.append(f"{subject}/{name}: {len(data['errors'])} questions errored")
                else:
                    failed.append(f"{subject}/{name}: answer evaluation failed")
            rows.append(row)

    seconds = round(time.time() - started)
    models = {role: trace_metadata(role)["model_id"] for role in ROLES}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = REPORTS / f"benchmark-{cfg.version}-{stamp}"  # the version has dots: no with_suffix
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "model_config_version": cfg.version,
        "serve_model": cfg.serve_model,
        "models": models,
        "options": vars(args),
        "rows": rows,
        "failed": failed,
        "seconds": seconds,
        "created_at": datetime.now(UTC).isoformat(),
    }
    Path(f"{out}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# Benchmark {stamp}",
        "",
        f"Models file version `{cfg.version}`; "
        + ", ".join(f"{role} `{m}`" for role, m in models.items()),
        f"Total time {seconds // 60} min {seconds % 60} s.",
        "",
        "| subject | set | v | group | recall@5 / MRR | answered | cites expected | judge grounded "
        "| forwarded (must) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        ret, ans = row.get("retrieval", {}), row.get("answers", {})
        groups = [g for g in ("easy", "hard", "exam", "check", "collected") if g in ret or g in ans]
        for g in [*groups, "abstain"]:
            if g == "abstain" and "abstain" not in ans:
                continue
            r, a = ret.get(g, {}), ans.get(g, {})
            recall = f"{r['recall@5']:.2f} / {r['mrr']:.2f}" if "recall@5" in r else ""
            lines.append(
                f"| {row['subject']} | {row['set']} | {row['kb_version']} | {g} "
                f"({a.get('n', r.get('n', ''))}) | {recall} | {a.get('answered', '')} "
                f"| {a.get('cited_expected', '')} | {a.get('grounded', '')} "
                f"| {a.get('forwarded', '')} |"
            )
    if failed:
        lines += ["", "**Failed:**", *[f"- {f}" for f in failed]]
    lines += [
        "",
        "Judge numbers are rough (CLAUDE.md, judge review); per-question details are in the "
        "reports next to this file, under the subject's folder.",
    ]
    Path(f"{out}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nsummary: {Path(f'{out}.md')}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
