"""The course's auto-generated check set in the database (CLAUDE.md "Evaluation strategy"),
built and scored by a background job after every processed version.

    run_checks(version):
      1. documents of the version without check questions (new or changed files) get some:
         about `check_questions` per course, spread over the material by slide count, written
         by the model from slides of that document (core.checkset);
      2. every valid question (not deleted, its file content still in the version) is searched
         in the version: did the slide it was written from come back in the top 5?

Questions belong to a document's *content* (file name + hash), so a carried-over document keeps
its questions and a changed one gets new ones. Questions a teacher deleted stay deleted.
Scoring uses search only (seconds): the answer-level check (grounding) needs the pilot GPU.
"""

import random
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.checkset import STYLES, Slide, eligible_slides, spread, write_question
from core.collected import collected_questions
from core.collected import found as found_rank
from core.db import app_engine, kb_scope
from core.models import CheckQuestion, Document, KbVersion, KnowledgeBase
from core.settings import get_settings
from llm.client import trace_metadata
from llm.prompts import prompt_version
from retrieval.search import Hit, search

K = 5
MAX_MISSES_SHOWN = 20


def _documents(version_id: uuid.UUID) -> dict[str, str]:
    with kb_scope(version_id) as s:
        return dict(s.execute(select(Document.file_name, Document.file_hash)).all())


def valid_questions(session: Session, kb_id: uuid.UUID, docs: dict[str, str]) -> list:
    """Not-deleted questions whose file content is in `docs` ({file_name: file_hash})."""
    rows = session.scalars(
        select(CheckQuestion)
        .where(CheckQuestion.kb_id == kb_id, CheckQuestion.deleted_at.is_(None))
        .order_by(CheckQuestion.file_name, CheckQuestion.page)
    ).all()
    return [q for q in rows if docs.get(q.file_name) == q.file_hash]


def plan(slides: list[Slide], covered: set[str]) -> dict[str, list[Slide]]:
    """Documents to write questions for (those not yet covered), with their slides."""
    by_doc: dict[str, list[Slide]] = defaultdict(list)
    for s in slides:
        by_doc[s.file_name].append(s)
    return {f: ss for f, ss in by_doc.items() if f not in covered}


def share(n_slides: int, all_slides: int, total: int) -> int:
    """A document's share of `total` questions, by its slides; at least one."""
    return max(1, round(total * n_slides / all_slides)) if all_slides else 0


def run_checks(
    version_id: uuid.UUID,
    *,
    total: int | None = None,
    write: Callable[[str, Slide, str], str | None] = write_question,
    search_fn: Callable[..., list[Hit]] = search,
    seed: int = 0,
    tries: int = 3,
) -> dict:
    """Write questions for uncovered documents, then score the version. Returns the report
    (also stored on the version)."""
    started = time.time()
    total = total if total is not None else get_settings().check_questions
    with Session(app_engine()) as s:
        version = s.get(KbVersion, version_id)
        kb = s.get(KnowledgeBase, version.kb_id)
        kb_id, subject = kb.id, kb.subject_name.replace("_", " ")
    docs = _documents(version_id)

    # 1. generate for documents whose content has no questions yet (deleted ones count as
    #    covered, so the teacher's deletions stick)
    with Session(app_engine()) as s:
        covered = {
            f
            for f, h in s.execute(
                select(CheckQuestion.file_name, CheckQuestion.file_hash).where(
                    CheckQuestion.kb_id == kb_id
                )
            ).all()
            if docs.get(f) == h
        }
    slides = eligible_slides(version_id)
    todo = plan(slides, covered)
    rng = random.Random(seed)
    styles = list(STYLES)
    trace = trace_metadata("answer") | {
        "prompt_version": prompt_version(["checkset_question"], "answer")
    }
    written = 0
    for file_name, doc_slides in sorted(todo.items()):
        n = share(len(doc_slides), len(slides), total)
        new = []
        for i, candidates in enumerate(spread(doc_slides, n, rng)):
            style = styles[(written + i) % len(styles)]
            for slide in candidates[:tries]:
                q = write(subject, slide, style)
                if q:
                    new.append(
                        CheckQuestion(
                            kb_id=kb_id,
                            question=q,
                            style=style,
                            file_name=file_name,
                            file_hash=docs[file_name],
                            page=slide.page,
                            created_for_version_id=version_id,
                            trace=trace,
                        )
                    )
                    break
        with Session(app_engine()) as s, s.begin():  # per document: progress survives a crash
            s.add_all(new)
        written += len(new)

    # 2. score: does search find the slide each question was written from?
    with Session(app_engine()) as s:
        valid = valid_questions(s, kb_id, docs)
        questions = [(q.id, q.question, q.file_name, q.page) for q in valid]
    found, rr, misses = 0, 0.0, []
    for qid, text, file_name, page in questions:
        hits = search_fn(version_id, text, K)
        rank = next(
            (i for i, h in enumerate(hits, 1) if h.file_name == file_name and page in h.pages),
            None,
        )
        if rank:
            found += 1
            rr += 1 / rank
        else:
            misses.append({"id": str(qid), "question": text, "file_name": file_name, "page": page})
    n = len(questions)

    # 3. the collected real questions (answered in the queue with a source): same search check
    with Session(app_engine()) as s:
        real = [c for c in collected_questions(s, kb_id, set(docs)) if c.kind == "answerable"]
    real_found, real_misses = 0, []
    for c in real:
        if found_rank(c.sources, search_fn(version_id, c.question, K)):
            real_found += 1
        else:
            real_misses.append({"id": str(c.id), "question": c.question})
    collected = {
        "questions": len(real),
        "found_top5": real_found,
        "recall_at_5": round(real_found / len(real), 3) if real else None,
        "misses": real_misses[:MAX_MISSES_SHOWN],
    }
    return {
        "collected": collected,
        "questions": n,
        "found_top5": found,
        "recall_at_5": round(found / n, 3) if n else None,
        "mrr": round(rr / n, 3) if n else None,
        "written": written,
        "misses": misses[:MAX_MISSES_SHOWN],
        "seconds": round(time.time() - started, 1),
        "models": {"answer": trace, "embedding": trace_metadata("embedding")},
        "created_at": datetime.now(UTC).isoformat(),
    }


def check_version(version_id: uuid.UUID, **kwargs) -> dict:
    """The job body: run the checks and record the status and report on the version."""
    _set(version_id, "running", None)
    try:
        report = run_checks(version_id, **kwargs)
    except Exception:
        _set(version_id, "failed", None)
        raise
    _set(version_id, "done", report)
    return report


def _set(version_id: uuid.UUID, status: str, report: dict | None) -> None:
    with Session(app_engine()) as s, s.begin():
        v = s.get(KbVersion, version_id)
        v.check_status = status
        if report is not None:
            v.check_report = report


def delete_question(session: Session, kb_id: uuid.UUID, question_id: uuid.UUID) -> bool:
    """Soft-delete (kept, so regeneration does not bring it back). False if not this course's."""
    q = session.get(CheckQuestion, question_id)
    if q is None or q.kb_id != kb_id or q.deleted_at is not None:
        return False
    q.deleted_at = datetime.now(UTC)
    return True
