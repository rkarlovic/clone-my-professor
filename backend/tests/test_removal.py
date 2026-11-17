"""Removing files: a teacher's removal is a draft; a file gone from every linked LMS course
leaves the live version by itself (the teacher's own uploads never); an empty LMS listing
removes nothing; a draft older than the live version cannot be approved any more."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from core.approval import (
    ApprovalError,
    activate_version,
    active_version,
    approve_version,
    auto_publish,
)
from core.db import kb_scope
from core.models import (
    Document,
    IngestStatus,
    KbVersion,
    KnowledgeBase,
    LmsFile,
    LtiPlatform,
    PlatformContextMap,
)
from core.settings import get_settings
from core.sync import sync_course
from ingestion.pipeline import process_version
from tests.test_sync import FakeLms

pytestmark = pytest.mark.db


def _docs(app_engine: Engine, version_id) -> dict[str, str]:
    with kb_scope(version_id, app_engine) as s:
        return dict(s.execute(select(Document.file_name, Document.source_platform)).all())


def _live(app_engine: Engine, kb_id) -> KbVersion:
    with Session(app_engine, expire_on_commit=False) as s:
        return active_version(s, kb_id)


def _publish_v1(app_engine: Engine, kb_id, files: dict[str, str]) -> uuid.UUID:
    """A published version 1 with documents {name: source_platform}."""
    with Session(app_engine) as s, s.begin():
        v = KbVersion(kb_id=kb_id, version=1, ingest_status=IngestStatus.ready)
        s.add(v)
        s.flush()
        vid = v.id
    with kb_scope(vid, app_engine) as s:
        for name, source in files.items():
            s.add(
                Document(
                    kb_version_id=vid, file_name=name, file_hash=f"h-{name}", source_platform=source
                )
            )
    with Session(app_engine) as s, s.begin():
        teacher = s.get(KnowledgeBase, kb_id).teacher_id
        approve_version(s, s.get(KbVersion, vid), teacher)
    with Session(app_engine) as s, s.begin():
        activate_version(s, s.get(KbVersion, vid))
    return vid


def _run_job(app_engine: Engine, version_id) -> None:
    """What the worker does for a removal version: copy, then publish if auto."""
    process_version(version_id)
    with Session(app_engine) as s, s.begin():
        auto_publish(s, s.get(KbVersion, version_id))


@pytest.fixture
def lms_link(owner_engine: Engine, course, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    kb_id, _ = course
    with Session(owner_engine) as s, s.begin():
        p = LtiPlatform(
            name="Fake LMS",
            issuer=f"https://fake-{uuid.uuid4().hex[:6]}.test",
            client_id="c",
            auth_login_url="x",
            jwks_url="x",
            ws_base_url="https://fake.test",
            ws_token_env="X",
        )
        s.add(p)
        s.flush()
        links = []
        for ctx in ("42", "43"):
            m = PlatformContextMap(platform_id=p.id, context_id=ctx, kb_id=kb_id, sync_enabled=True)
            s.add(m)
            s.flush()
            links.append(m.id)
        pid = p.id
    yield links, kb_id
    with owner_engine.begin() as c:
        for m in links:
            c.execute(text("DELETE FROM lms_file WHERE map_id = :m"), {"m": m})
            c.execute(text("DELETE FROM platform_context_map WHERE id = :m"), {"m": m})
        c.execute(text("DELETE FROM lti_platform WHERE id = :p"), {"p": pid})


def _seen(app_engine: Engine, map_id, name: str) -> None:
    with Session(app_engine) as s, s.begin():
        s.add(
            LmsFile(
                map_id=map_id,
                external_id=f"x/{name}",
                file_name=name,
                filesize=1,
                timemodified=1,
                file_hash=f"h-{name}",
            )
        )


def test_a_file_gone_from_the_lms_leaves_the_live_version(app_engine: Engine, lms_link) -> None:
    (link, other), kb_id = lms_link
    _publish_v1(
        app_engine,
        kb_id,
        {"a.pdf": "lms", "b.pdf": "lms", "mine.pdf": "upload", "shared.pdf": "lms"},
    )
    _seen(app_engine, other, "shared.pdf")  # still in the other linked LMS course
    lms = FakeLms({"1/a.pdf": ("a.pdf", b"%PDF a", 1)})  # b.pdf deleted or hidden in the LMS
    queued: list = []

    r = sync_course(link, connector_factory=lambda p: lms, enqueue=lambda v, k: queued.append(v))
    assert r["status"] == "removed" and r["removed_files"] == ["b.pdf"] and r["published"]
    _run_job(app_engine, queued[0])
    live = _live(app_engine, kb_id)
    assert live.version == 2 and live.approved_by is None  # published by the system
    assert set(_docs(app_engine, live.id)) == {"a.pdf", "mine.pdf", "shared.pdf"}


def test_an_empty_lms_listing_removes_nothing(app_engine: Engine, lms_link) -> None:
    (link, _), kb_id = lms_link
    _publish_v1(app_engine, kb_id, {"a.pdf": "lms", "b.pdf": "lms"})
    r = sync_course(link, connector_factory=lambda p: FakeLms({}), enqueue=lambda v, k: None)
    assert r["status"] == "unchanged" and "nothing removed" in r["removal_skipped"]
    assert _live(app_engine, kb_id).version == 1


def test_a_teacher_removal_is_a_draft_and_old_drafts_cannot_be_approved(
    app_engine: Engine, course
) -> None:
    from app import courses
    from app.main import app

    kb_id, teacher = course
    _publish_v1(app_engine, kb_id, {"a.pdf": "upload", "b.pdf": "upload"})
    http, me = TestClient(app), {"X-CMP-Dev-Teacher": teacher}
    queued: list = []
    original = courses.enqueue_ingest
    courses.enqueue_ingest = lambda v, k: queued.append(v)
    try:
        r = http.post(f"/api/courses/{kb_id}/remove", json={"file_names": ["b.pdf"]}, headers=me)
        assert r.status_code == 202 and r.json()["removed_files"] == ["b.pdf"]
        last = http.post(
            f"/api/courses/{kb_id}/remove", json={"file_names": ["a.pdf", "b.pdf"]}, headers=me
        )
        assert last.status_code == 409  # still processing the first removal
    finally:
        courses.enqueue_ingest = original
    _run_job(app_engine, queued[0])
    assert _live(app_engine, kb_id).version == 1  # a teacher's removal waits for approval
    with Session(app_engine) as s:
        v2 = s.query(KbVersion).filter_by(kb_id=kb_id, version=2).one()
        assert v2.status.value == "draft" and set(_docs(app_engine, v2.id)) == {"a.pdf"}
    versions = http.get(f"/api/courses/{kb_id}/versions", headers=me).json()
    assert [v["is_base"] for v in versions] == [True, False]  # the draft is what builds on next
    assert (
        http.post(
            f"/api/courses/{kb_id}/remove", json={"file_names": ["a.pdf"]}, headers=me
        ).status_code
        == 409
    )

    # a draft older than the live version can no longer be approved
    with Session(app_engine) as s, s.begin():
        v3 = KbVersion(kb_id=kb_id, version=3, ingest_status=IngestStatus.ready)
        s.add(v3)
        s.flush()
        approve_version(s, v3, None)
        activate_version(s, v3)
    with Session(app_engine) as s, s.begin(), pytest.raises(ApprovalError, match="older than"):
        approve_version(s, s.query(KbVersion).filter_by(kb_id=kb_id, version=2).one(), None)


def test_a_file_gone_from_the_lms_is_also_taken_out_of_a_waiting_draft(
    app_engine: Engine, lms_link
) -> None:
    (link, _), kb_id = lms_link
    _publish_v1(app_engine, kb_id, {"mine.pdf": "upload"})
    lms = FakeLms({"1/a.pdf": ("a.pdf", b"%PDF a", 1), "2/b.pdf": ("b.pdf", b"%PDF b", 1)})
    queued: list = []

    def run():
        return sync_course(
            link, connector_factory=lambda p: lms, enqueue=lambda v, k: queued.append(v)
        )

    assert run()["status"] == "new_version"  # v2: a draft with a.pdf and b.pdf
    with Session(app_engine) as s, s.begin():  # pretend the ingest ran (copy + the two files)
        v2 = s.query(KbVersion).filter_by(kb_id=kb_id, version=2).one()
        v2.ingest_status = IngestStatus.ready
        v2_id = v2.id
    with kb_scope(v2_id, app_engine) as s:
        for name, source in (("mine.pdf", "upload"), ("a.pdf", "lms"), ("b.pdf", "lms")):
            s.add(
                Document(
                    kb_version_id=v2_id,
                    file_name=name,
                    file_hash=f"h-{name}",
                    source_platform=source,
                )
            )

    del lms.files["2/b.pdf"]  # hidden or deleted in the LMS before the teacher approved v2
    r = run()
    assert r["status"] == "removed" and r["removed_files"] == ["b.pdf"] and not r["published"]
    _run_job(app_engine, queued[-1])
    assert _live(app_engine, kb_id).version == 1  # a draft, not published
    with Session(app_engine) as s:
        v3 = s.query(KbVersion).filter_by(kb_id=kb_id, version=3).one()
        assert v3.status.value == "draft" and set(_docs(app_engine, v3.id)) == {"mine.pdf", "a.pdf"}
