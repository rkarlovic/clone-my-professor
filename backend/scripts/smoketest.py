"""Smoke test: does this installation (or a newly chosen model) still work end to end?

    uv run python -m scripts.smoketest            # dev: the backend on the host
    docker compose ... exec api python -m scripts.smoketest     # an installation
    .\\setup.ps1 -Check   /   ./setup.sh --check                # the same, from the setup script
    options: --ids s01,s08   --keep (leave the test course in place)   --build (rebuild the .pptx)

Uses CMP's own small course (smoke/course.yaml: 14 slides on beekeeping basics, written for the
project, so it ships with every installation) and 10 questions: 7 that must be answered citing
the right slide (worded like students: casual, without diacritics, in English) and 3 that must
go to the teacher (points, attendance, another subject). Every part runs for real: Docling
parsing, the embedding model, the database with row-level security, and every chat role
(scope check, answer, answer check). Nothing is stored as a student exchange and nothing goes to
anyone's queue: the questions are asked through the answer pipeline directly. The test course
is created under a dedicated teacher and removed afterwards.

Passes when all 3 forward questions are forwarded and at least 5 of the 7 others are answered
citing their slide (the dev 9B on CPU varies by a question between runs). Exit code 0 / 1.
Time: about a minute per answered question on a CPU laptop (~12 min for all 10); seconds on a GPU.
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import yaml
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from core.access import create_course
from core.answer import answer_question
from core.approval import activate_version, approve_version
from core.db import app_engine
from core.kb import create_upload_version
from core.models import IngestStatus, KbVersion, KnowledgeBase, Teacher
from core.settings import BACKEND_DIR, get_settings
from core.storage import store_upload
from llm.config import get_model_config

SMOKE_DIR = BACKEND_DIR / "smoke"
TEACHER_REF = "cmp-smoke-test"  # a fixed external_ref: never a real person's hash
MIN_ANSWERED = 5


def build(spec: dict, out: Path) -> None:
    """The course file from the YAML (python-pptx comes with Docling's ingest group)."""
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    for slide in spec["slides"]:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = slide["title"]
        body = s.placeholders[1].text_frame
        for i, bullet in enumerate(slide["bullets"]):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = bullet
            p.font.size = Pt(20)
    prs.save(out)


def _teacher(s: Session) -> uuid.UUID:
    t = s.scalars(select(Teacher).where(Teacher.external_ref == TEACHER_REF)).first()
    if t is None:
        t = Teacher(name="CMP provjera instalacije", external_ref=TEACHER_REF)
        s.add(t)
        s.flush()
    return t.id


def publish(spec: dict, path: Path) -> tuple[uuid.UUID, uuid.UUID]:
    """A new course with the file, processed, approved and live: (kb_id, version_id)."""
    from ingestion.pipeline import process_version  # Docling: only when it really runs

    stamp = time.strftime("%Y-%m-%d %H:%M")
    with Session(app_engine()) as s, s.begin():
        teacher_id = _teacher(s)
        kb = create_course(s, teacher_id, f"{spec['subject']} {stamp}")
        kb_id = kb.id
    with path.open("rb") as f:
        stored = store_upload(kb_id, spec["file"], f)
    with Session(app_engine()) as s, s.begin():
        version = create_upload_version(s, s.get(KnowledgeBase, kb_id), [stored])
        version_id = version.id
    process_version(version_id)
    with Session(app_engine()) as s, s.begin():
        v = s.get(KbVersion, version_id)
        if v.ingest_status != IngestStatus.ready:
            raise SystemExit(f"processing the course failed: {v.ingest_error}")
        approve_version(s, v, teacher_id)
    with Session(app_engine()) as s, s.begin():
        activate_version(s, s.get(KbVersion, version_id))
    return kb_id, version_id


def remove(kb_id: uuid.UUID) -> None:
    """The test course and everything under it (as the owner role: course deletion is not a
    teacher action yet)."""
    engine = create_engine(get_settings().database_owner_url)
    p = {"k": kb_id}
    versions = "SELECT id FROM kb_version WHERE kb_id = :k"
    with engine.begin() as c:
        for table in ("check_question", "persona_config", "chat_message", "teacher_question"):
            c.execute(text(f"DELETE FROM {table} WHERE kb_id = :k"), p)
        for table in ("notification", "course_teacher"):
            c.execute(text(f"DELETE FROM {table} WHERE kb_id = :k"), p)
        c.execute(text(f"DELETE FROM chunk WHERE kb_version_id IN ({versions})"), p)
        c.execute(text(f"DELETE FROM document WHERE kb_version_id IN ({versions})"), p)
        c.execute(text("UPDATE kb_version SET base_version_id = NULL WHERE kb_id = :k"), p)
        c.execute(text("DELETE FROM kb_version WHERE kb_id = :k"), p)
        c.execute(text("DELETE FROM knowledge_base WHERE id = :k"), p)
    engine.dispose()


def judge(q: dict, a) -> tuple[bool, str]:
    if q["expect"] == "forward":
        if a.forwarded:
            return True, f"forwarded ({a.forwarded.value})"
        return False, "ANSWERED, but must go to the teacher"
    if a.forwarded:
        return False, f"forwarded ({a.forwarded.value}), but the course answers it"
    pages = sorted({p for s in a.cited for p in s.pages})
    if q["slide"] in pages:
        return True, f"answered, cites slide {q['slide']}"
    return False, f"answered citing {pages or 'nothing'}, expected slide {q['slide']}"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="only these questions, e.g. s01,s08")
    ap.add_argument("--keep", action="store_true", help="leave the test course in place")
    ap.add_argument("--build", action="store_true", help="rebuild the course .pptx and stop")
    args = ap.parse_args()
    spec = yaml.safe_load((SMOKE_DIR / "course.yaml").read_text(encoding="utf-8"))
    path = SMOKE_DIR / spec["file"]
    if args.build or not path.exists():
        build(spec, path)
        print(f"built {path.name}")
        if args.build:
            return 0
    questions = spec["questions"]
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    cfg = get_model_config()
    print(
        f"CMP smoke test · models {cfg.version}: answer {cfg.role('answer').model}, "
        f"check {cfg.role('check').model}, embedding {cfg.role('embedding').model}"
    )
    started = time.time()
    kb_id, version_id = publish(spec, path)
    print(f"course processed and published in {time.time() - started:.0f} s")
    results = []
    try:
        for q in questions:
            t = time.time()
            try:
                a = answer_question(version_id, q["question"], source="smoke")
                ok, note = judge(q, a)
            except Exception as e:  # noqa: BLE001 - a model error is a failed check, reported
                ok, note = False, f"ERROR {type(e).__name__}: {str(e)[:120]}"
            results.append((q, ok))
            print(f"  {'ok  ' if ok else 'FAIL'} {q['id']} {time.time() - t:5.0f} s  {note}")
            print(f"       „{q['question']}”")
    finally:
        if not args.keep:
            remove(kb_id)
    answers = [ok for q, ok in results if q["expect"] == "answer"]
    forwards = [ok for q, ok in results if q["expect"] == "forward"]
    need = min(MIN_ANSWERED, len(answers))
    passed = all(forwards) and sum(answers) >= need
    print(
        f"\n{'PASSED' if passed else 'FAILED'}: answered with the right slide {sum(answers)}/"
        f"{len(answers)} (need {need}), forwarded {sum(forwards)}/{len(forwards)} (need all) · "
        f"{time.time() - started:.0f} s"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
