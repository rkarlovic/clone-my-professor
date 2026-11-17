"""Direct upload (build step 3): file store, draft versions from uploads, carry-over of
unchanged documents, and the teacher API. The ingest job itself (Docling, embeddings) is not
run here; the API's enqueue call is replaced by a recorder."""

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from core.db import kb_scope
from core.kb import NothingChanged, StillProcessing, create_upload_version
from core.models import Chunk, Document, IngestStatus, KbVersion, KnowledgeBase
from core.settings import get_settings
from core.storage import StoredFile, UploadError, clean_file_name, store_upload
from ingestion.pipeline import copy_documents


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    return tmp_path


# --- file store (no database) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\x\\Predavanje 1.pptx", "Predavanje 1.pptx"),
        ("  7 Identifikacija.pptx ", "7 Identifikacija.pptx"),
        ("a\x00b.pdf", "ab.pdf"),
    ],
)
def test_file_names_never_carry_a_path(raw: str, clean: str) -> None:
    assert clean_file_name(raw) == clean


def test_store_keeps_one_copy_per_content(upload_dir: Path) -> None:
    kb = uuid.uuid4()
    a = store_upload(kb, "a.pdf", io.BytesIO(b"%PDF-1.7 same"))
    b = store_upload(kb, "b.pdf", io.BytesIO(b"%PDF-1.7 same"))
    assert a.path == b.path and a.path.parent == upload_dir / str(kb)
    assert (a.file_name, b.file_name) == ("a.pdf", "b.pdf")
    assert [p.name for p in a.path.parent.iterdir()] == [a.path.name]  # no .part leftovers


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("notes.txt", b"x", "not supported"),
        ("empty.pdf", b"", "empty"),
        ("big.pdf", b"x" * (1024 * 1024 + 1), "larger than 1 MB"),
    ],
)
def test_store_refuses_bad_files(
    upload_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: bytes, message: str
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    with pytest.raises(UploadError, match=message):
        store_upload(uuid.uuid4(), name, io.BytesIO(data))
    assert not any(p.suffix != "" for p in upload_dir.rglob("*") if p.is_file())


# --- versions from uploads (database) -------------------------------------------------------


@pytest.fixture
def course(owner_engine: Engine) -> Iterator[tuple[uuid.UUID, str]]:
    """A teacher with one empty course; everything under it is removed afterwards."""
    teacher, kb, name = uuid.uuid4(), uuid.uuid4(), f"test-{uuid.uuid4().hex[:8]}"
    with owner_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO teacher (id, name) VALUES (:t, :n)"), {"t": teacher, "n": name}
        )
        conn.execute(
            text("INSERT INTO knowledge_base (id, teacher_id, subject_name) VALUES (:k, :t, 'T')"),
            {"k": kb, "t": teacher},
        )
    yield kb, name
    with owner_engine.begin() as conn:
        p = {"k": kb}
        versions = "SELECT id FROM kb_version WHERE kb_id = :k"
        conn.execute(text(f"DELETE FROM chunk WHERE kb_version_id IN ({versions})"), p)
        conn.execute(text(f"DELETE FROM document WHERE kb_version_id IN ({versions})"), p)
        conn.execute(text("UPDATE kb_version SET base_version_id = NULL WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM kb_version WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM knowledge_base WHERE id = :k"), p)
        conn.execute(text("DELETE FROM teacher WHERE name = :n"), {"n": name})


def _ready_version_with(app_engine: Engine, kb_id: uuid.UUID, files: dict[str, str]) -> uuid.UUID:
    """A processed version holding documents {file_name: file_hash}, each with a parent and a
    child chunk."""
    with Session(app_engine) as s, s.begin():
        v = KbVersion(kb_id=kb_id, version=1, ingest_status=IngestStatus.ready)
        s.add(v)
        s.flush()
        vid = v.id
    with kb_scope(vid, app_engine) as s:
        for name, digest in files.items():
            d = Document(kb_version_id=vid, file_name=name, file_hash=digest)
            s.add(d)
            s.flush()
            parent = Chunk(kb_version_id=vid, document_id=d.id, text=f"{name} section")
            s.add(parent)
            s.flush()
            s.add(
                Chunk(
                    kb_version_id=vid,
                    document_id=d.id,
                    parent_id=parent.id,
                    text=f"{name} slide",
                    page_or_slide=3,
                    embedding=[0.5] * 1024,
                )
            )
    return vid


def _stored(name: str, digest: str) -> StoredFile:
    return StoredFile(name, digest, Path(f"/store/{digest}.pdf"), 1)


@pytest.mark.db
def test_upload_version_processes_only_new_or_changed_files(app_engine: Engine, course) -> None:
    kb_id, _ = course
    base = _ready_version_with(app_engine, kb_id, {"a.pdf": "h-a", "b.pdf": "h-b"})
    with Session(app_engine) as s, s.begin():
        kb = s.get(KnowledgeBase, kb_id)
        with pytest.raises(NothingChanged):
            create_upload_version(s, kb, [_stored("a.pdf", "h-a")])
    with Session(app_engine) as s, s.begin():
        kb = s.get(KnowledgeBase, kb_id)
        v = create_upload_version(
            s, kb, [_stored("a.pdf", "h-a"), _stored("b.pdf", "h-b2"), _stored("c.pdf", "h-c")]
        )
        assert (v.version, v.base_version_id, v.ingest_status) == (2, base, IngestStatus.queued)
        assert [f["file_name"] for f in v.pending_files] == ["b.pdf", "c.pdf"]
    with Session(app_engine) as s, s.begin():  # v2 is queued: a third upload must wait
        kb = s.get(KnowledgeBase, kb_id)
        with pytest.raises(StillProcessing):
            create_upload_version(s, kb, [_stored("d.pdf", "h-d")])


@pytest.mark.db
def test_carry_over_copies_documents_and_chunks_into_the_new_version(
    app_engine: Engine, course
) -> None:
    kb_id, _ = course
    base = _ready_version_with(app_engine, kb_id, {"a.pdf": "h-a", "b.pdf": "h-b"})
    with Session(app_engine) as s, s.begin():
        new = KbVersion(kb_id=kb_id, version=2, base_version_id=base)
        s.add(new)
        s.flush()
        new_id = new.id
    assert copy_documents(base, new_id, {"a.pdf"}) == ["a.pdf"]
    with kb_scope(new_id, app_engine) as s:
        (doc,) = s.scalars(select(Document)).all()
        chunks = s.scalars(select(Chunk)).all()
        assert (doc.file_name, doc.file_hash) == ("a.pdf", "h-a")
        assert {c.document_id for c in chunks} == {doc.id}
        child = next(c for c in chunks if c.parent_id)
        parent = next(c for c in chunks if not c.parent_id)
        assert child.parent_id == parent.id and child.page_or_slide == 3
        assert child.embedding is not None and float(child.embedding[0]) == 0.5
    with kb_scope(base, app_engine) as s:  # the base version is untouched
        assert len(s.scalars(select(Document)).all()) == 2
        assert all(c.kb_version_id == base for c in s.scalars(select(Chunk)).all())


# --- teacher API --------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list]]:
    from app import courses
    from app.main import app

    queued: list = []
    monkeypatch.setattr(courses, "enqueue_ingest", lambda v, k: queued.append((v, k)) or 1)
    yield TestClient(app), queued  # no `with`: the lifespan (real queue) is not started


@pytest.mark.db
def test_api_upload_creates_a_queued_draft_and_only_the_owner_sees_it(
    app_engine: Engine, course, client, upload_dir: Path
) -> None:
    kb_id, teacher = course
    http, queued = client
    me, other = {"X-CMP-Dev-Teacher": teacher}, {"X-CMP-Dev-Teacher": teacher + "-other"}
    files = [("files", ("1 Uvod.pdf", b"%PDF-1.7 lecture one", "application/pdf"))]

    assert http.post(f"/api/courses/{kb_id}/versions", files=files).status_code == 401
    url = f"/api/courses/{kb_id}/versions"
    assert http.post(url, files=files, headers=other).status_code == 404

    r = http.post(f"/api/courses/{kb_id}/versions", files=files, headers=me)
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["version"], body["status"], body["ingest_status"]) == (1, "draft", "queued")
    assert body["pending_files"] == ["1 Uvod.pdf"]
    assert len(queued) == 1 and queued[0][1] == kb_id

    r = http.post(f"/api/courses/{kb_id}/versions", files=files, headers=me)
    assert r.status_code == 409 and "still being processed" in r.json()["detail"]
    bad = [("files", ("notes.txt", b"x", "text/plain"))]
    assert http.post(f"/api/courses/{kb_id}/versions", files=bad, headers=me).status_code == 422

    listed = http.get(f"/api/courses/{kb_id}/versions", headers=me).json()
    assert [v["version"] for v in listed] == [1]
    assert http.get(f"/api/courses/{kb_id}/versions", headers=other).status_code == 404
    assert http.get(f"/api/courses/{kb_id}/versions/1", headers=me).json()["documents"] is None
    retry = http.post(f"/api/courses/{kb_id}/versions/1/retry", headers=me)
    assert retry.status_code == 409  # only a failed version can be retried


def test_failed_file_message_names_the_file_not_the_server_path() -> None:
    from ingestion.pipeline import FileFailed

    class ConversionError(Exception):
        pass

    msg = str(FileFailed("1 Uvod.pdf", ConversionError("/srv/cmp/data/uploads/ab12.pdf failed")))
    assert msg.startswith("1 Uvod.pdf: the file could not be read")
    assert "uploads" not in msg
    assert str(FileFailed("2.pptx", KeyError("x"))) == "2.pptx: processing failed (KeyError)"
