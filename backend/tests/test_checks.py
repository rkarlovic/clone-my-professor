"""Auto-generated check set in the database: questions only for new or changed documents,
carried-over documents keep theirs, deletions stick, the version is scored by search."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core.checks import check_version, run_checks, share
from core.db import kb_scope
from core.models import CheckQuestion, Chunk, Document, IngestStatus, KbVersion
from retrieval.search import Hit

pytestmark = pytest.mark.db
LONG = " ".join(["riječ"] * 40)  # eligible slides need at least 30 words


def _version(app_engine: Engine, kb_id, number: int, docs: dict[str, tuple[str, int]]):
    """A processed version with documents {file_name: (hash, slide count)}."""
    with Session(app_engine) as s, s.begin():
        v = KbVersion(kb_id=kb_id, version=number, ingest_status=IngestStatus.ready)
        s.add(v)
        s.flush()
        vid = v.id
    with kb_scope(vid, app_engine) as s:
        for name, (digest, slides) in docs.items():
            d = Document(kb_version_id=vid, file_name=name, file_hash=digest)
            s.add(d)
            s.flush()
            for page in range(1, slides + 1):
                s.add(
                    Chunk(
                        kb_version_id=vid,
                        document_id=d.id,
                        text=f"{name} {page} {LONG}",
                        page_or_slide=page,
                        embedding=[0.1] * 1024,
                    )
                )
    return vid


def _writer(log: list):
    def write(subject, slide, style):
        log.append((slide.file_name, slide.page, style))
        return f"Pitanje o {slide.file_name} {slide.page}?"

    return write


def _search(found: bool = True):
    """Finds the slide a question names (by its text), or never."""

    def search_fn(version_id, question, k):
        if not found:
            return []
        _, name, page = question.rstrip("?").rsplit(" ", 2)
        return [Hit(uuid.uuid4(), 0.8, "t", "t", "", name, int(page), "other")]

    return search_fn


def _questions(app_engine: Engine, kb_id, deleted: bool = False) -> list[CheckQuestion]:
    with Session(app_engine) as s:
        q = select(CheckQuestion).where(CheckQuestion.kb_id == kb_id)
        q = q.where(
            CheckQuestion.deleted_at.is_not(None) if deleted else CheckQuestion.deleted_at.is_(None)
        )
        return list(s.scalars(q))


def test_share_spreads_questions_by_slide_count() -> None:
    assert share(30, 60, 60) == 30 and share(1, 600, 60) == 1 and share(5, 0, 60) == 0


def test_questions_follow_document_content_and_deletions_stick(app_engine: Engine, course) -> None:
    kb_id, _ = course
    v1 = _version(app_engine, kb_id, 1, {"a.pdf": ("h-a", 6), "b.pdf": ("h-b", 4)})
    written: list = []
    r1 = run_checks(v1, total=5, write=_writer(written), search_fn=_search())
    assert r1["written"] == 5 and r1["questions"] == 5 and r1["recall_at_5"] == 1.0
    assert {f for f, _, _ in written} == {"a.pdf", "b.pdf"}
    assert len({style for _, _, style in written}) == 5  # styles rotate

    # the teacher deletes one question about a.pdf
    a_question = next(q for q in _questions(app_engine, kb_id) if q.file_name == "a.pdf")
    with Session(app_engine) as s, s.begin():
        from core.checks import delete_question

        assert delete_question(s, kb_id, a_question.id)

    # v2: a.pdf unchanged (carried over), b.pdf changed, c.pdf new
    v2 = _version(
        app_engine, kb_id, 2, {"a.pdf": ("h-a", 6), "b.pdf": ("h-b2", 4), "c.pdf": ("h-c", 2)}
    )
    written.clear()
    r2 = run_checks(v2, total=6, write=_writer(written), search_fn=_search(found=False))
    assert {f for f, _, _ in written} == {"b.pdf", "c.pdf"}  # a.pdf keeps its questions
    assert r2["found_top5"] == 0 and r2["recall_at_5"] == 0.0
    names = [m["file_name"] for m in r2["misses"]]
    assert "a.pdf" in names and a_question.id not in {uuid.UUID(m["id"]) for m in r2["misses"]}
    old_b = [q for q in _questions(app_engine, kb_id) if q.file_hash == "h-b"]
    assert all(
        m["file_name"] != "b.pdf" or m["id"] not in {str(q.id) for q in old_b} for m in r2["misses"]
    )
    assert len(_questions(app_engine, kb_id, deleted=True)) == 1


def test_job_records_status_and_report_and_the_api_lists_and_deletes(
    app_engine: Engine, course, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    kb_id, teacher = course
    v1 = _version(app_engine, kb_id, 1, {"a.pdf": ("h-a", 3)})
    check_version(v1, total=2, write=_writer([]), search_fn=_search())
    with Session(app_engine) as s:
        v = s.get(KbVersion, v1)
        assert v.check_status == "done" and v.check_report["found_top5"] == 2

    http, me = TestClient(app), {"X-CMP-Dev-Teacher": teacher}
    version = http.get(f"/api/courses/{kb_id}/versions/1", headers=me).json()
    assert version["check_status"] == "done" and version["check"]["questions"] == 2
    listed = http.get(f"/api/courses/{kb_id}/checks", headers=me).json()
    assert len(listed) == 2 and listed[0]["file_name"] == "a.pdf"
    other = {"X-CMP-Dev-Teacher": teacher + "-other"}
    assert (
        http.delete(f"/api/courses/{kb_id}/checks/{listed[0]['id']}", headers=other).status_code
        == 404
    )
    assert (
        http.delete(f"/api/courses/{kb_id}/checks/{listed[0]['id']}", headers=me).status_code == 204
    )
    assert (
        http.delete(f"/api/courses/{kb_id}/checks/{listed[0]['id']}", headers=me).status_code == 404
    )
    assert len(http.get(f"/api/courses/{kb_id}/checks", headers=me).json()) == 1
