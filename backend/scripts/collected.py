"""Export a course's collected real questions (core/collected.py) as a question set, to run with
scripts.evaluate / scripts.evaluate_answers --set.

    uv run python -m scripts.collected --subject <folder or course name> [--version N] [--out PATH]

Written to eval/sets/local/<subject>.collected.yaml (git-ignored): it holds student text and
never leaves the installation (rule 9). No student reference is exported.
"""

import argparse
import sys
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from core.collected import collected_questions, to_eval_set
from core.db import app_engine, kb_scope
from core.kb import find_version
from core.models import Document
from core.settings import REPO_ROOT


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--version", type=int)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    with Session(app_engine()) as s:
        version = find_version(s, args.subject, args.version)
        kb_id, version_id = version.kb_id, version.id
    with kb_scope(version_id) as s:
        files = set(s.scalars(Document.__table__.select().with_only_columns(Document.file_name)))
    with Session(app_engine()) as s:
        items = collected_questions(s, kb_id, files)
    out = args.out or REPO_ROOT / "eval" / "sets" / "local" / f"{args.subject}.collected.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Collected real questions (teacher's queue answers with a source). Student text:\n"
        "# stays in this installation, never commit or share (rule 9).\n"
    )
    body = yaml.safe_dump(to_eval_set(items, args.subject), allow_unicode=True, sort_keys=False)
    out.write_text(header + body, encoding="utf-8")
    answerable = sum(c.kind == "answerable" for c in items)
    print(
        f"{len(items)} questions ({answerable} with a source, "
        f"{len(items) - answerable} to forward) -> {out}"
    )


if __name__ == "__main__":
    main()
