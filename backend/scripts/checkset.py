"""Generate a subject's auto-generated check set (CLAUDE.md "Evaluation strategy").

    uv run python -m scripts.checkset --subject <id> [--version N] [-n 60] [--seed 0] [--force]

Writes eval/sets/local/<subject>.check.yaml (git-ignored: it paraphrases the installation's own
material). Run it like any other set:
    uv run python -m scripts.evaluate --subject <id> --set ../eval/sets/local/<id>.check.yaml
    uv run python -m scripts.evaluate_answers --subject <id> --set ... [--judge]
The teacher may delete bad questions from the file; regenerating needs --force, because it
would bring them back.
"""

import argparse
import sys
import time
from datetime import UTC, datetime

import yaml
from sqlalchemy.orm import Session

from core.checkset import STYLES, generate
from core.db import app_engine
from core.kb import find_version
from core.settings import REPO_ROOT
from llm.client import trace_metadata
from llm.prompts import prompt_version


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int)
    ap.add_argument("-n", type=int, default=60, help="slide questions (plus the generic ones)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="overwrite an existing check set")
    args = ap.parse_args()

    out = REPO_ROOT / "eval" / "sets" / "local" / f"{args.subject}.check.yaml"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists (a teacher may have edited it); pass --force to replace")
    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)

    started = time.time()

    def progress(i: int, last: dict | None) -> None:
        print(
            f"[{i}/{args.n}] {last['id'] if last else '-'}: {last['question'] if last else ''}",
            flush=True,
        )

    questions = generate(
        version.id, args.subject.replace("_", " "), args.n, args.seed, progress=progress
    )
    seconds = round(time.time() - started, 1)
    data = {
        "subject_id": args.subject,
        "subject_name": args.subject.replace("_", " "),
        "language": "hr",
        "generated": {
            "kind": "auto-generated check set",
            "kb_version": version.version,
            "model": trace_metadata("answer"),
            "prompt_version": prompt_version(["checkset_question"], "answer"),
            "styles": list(STYLES),
            "n": args.n,
            "seed": args.seed,
            "seconds": seconds,
            "created_at": datetime.now(UTC).isoformat(),
        },
        "questions": questions,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Auto-generated check set: questions written by a model from the slides; the slide is\n"
        "# the expected source. Local only (made from this installation's material).\n"
        "# Delete questions that are bad; nothing else needs doing.\n"
    )
    out.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    answerable = sum(q["type"] == "answerable" for q in questions)
    print(f"{answerable} slide questions + {len(questions) - answerable} generic in {seconds:.0f}s")
    print(f"set: {out}")


if __name__ == "__main__":
    main()
