"""Rule 5: nothing reaches students without the teacher's approval. Approval, activation (one
active version, rollback), discarding drafts, and which version an upload builds on."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core.approval import (
    ApprovalError,
    activate_version,
    active_version,
    approve_version,
    discard_version,
)
from core.db import kb_scope
from core.kb import base_version
from core.models import Chunk, Document, IngestStatus, KbVersion, KbVersionStatus, KnowledgeBase
from tests.conftest import ready_version_with

pytestmark = pytest.mark.db


def _teacher(app_engine: Engine, kb_id: uuid.UUID) -> uuid.UUID:
    with Session(app_engine) as s:
        return s.get(KnowledgeBase, kb_id).teacher_id


def _do(app_engine: Engine, version_id: uuid.UUID, step) -> KbVersion:
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        v = s.get(KbVersion, version_id)
        step(s, v)
        return v


def _status(app_engine: Engine, version_id: uuid.UUID) -> KbVersionStatus:
    with Session(app_engine) as s:
        return s.get(KbVersion, version_id).status


def test_approve_then_activate_and_roll_back(app_engine: Engine, course) -> None:
    kb_id, _ = course
    teacher = _teacher(app_engine, kb_id)
    v1 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a"}, version=1)
    v2 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a2"}, version=2)

    with pytest.raises(ApprovalError, match="must be approved"):
        _do(app_engine, v1, activate_version)
    approved = _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))
    assert approved.status == KbVersionStatus.approved and approved.approved_by == teacher
    with kb_scope(v1, app_engine) as s:
        assert all(d.approved_at is not None for d in s.scalars(select(Document)))
    with Session(app_engine) as s:
        assert active_version(s, kb_id) is None  # approved is not yet live

    _do(app_engine, v1, activate_version)
    _do(app_engine, v2, lambda s, v: approve_version(s, v, teacher))
    _do(app_engine, v2, activate_version)
    assert (_status(app_engine, v1), _status(app_engine, v2)) == (
        KbVersionStatus.approved,
        KbVersionStatus.active,
    )
    _do(app_engine, v1, activate_version)  # rollback
    with Session(app_engine) as s:
        assert active_version(s, kb_id).id == v1
    assert _status(app_engine, v2) == KbVersionStatus.approved
    with pytest.raises(ApprovalError, match="already active"):
        _do(app_engine, v1, activate_version)


def test_only_a_processed_draft_can_be_approved(app_engine: Engine, course) -> None:
    kb_id, _ = course
    teacher = _teacher(app_engine, kb_id)
    v1 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a"})
    with Session(app_engine) as s, s.begin():
        s.get(KbVersion, v1).ingest_status = IngestStatus.processing
    with pytest.raises(ApprovalError, match="only a processed version"):
        _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))
    with Session(app_engine) as s, s.begin():
        s.get(KbVersion, v1).ingest_status = IngestStatus.ready
    _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))
    with pytest.raises(ApprovalError, match="already approved"):
        _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))


def test_discard_removes_a_draft_but_never_an_approved_version(app_engine: Engine, course) -> None:
    kb_id, _ = course
    teacher = _teacher(app_engine, kb_id)
    v1 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a"}, version=1)
    v2 = ready_version_with(app_engine, kb_id, {"b.pdf": "h-b"}, version=2)
    _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))
    with pytest.raises(ApprovalError, match="only a draft"):
        _do(app_engine, v1, discard_version)

    _do(app_engine, v2, discard_version)
    with Session(app_engine) as s:
        assert s.get(KbVersion, v2) is None
    with kb_scope(v2, app_engine) as s:
        assert s.scalars(select(Chunk)).all() == []
    with kb_scope(v1, app_engine) as s:  # the other version is untouched
        assert len(s.scalars(select(Chunk)).all()) == 2


def test_uploads_stack_on_unreviewed_drafts_and_follow_a_rollback(
    app_engine: Engine, course
) -> None:
    kb_id, _ = course
    teacher = _teacher(app_engine, kb_id)
    v1 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a"}, version=1)
    _do(app_engine, v1, lambda s, v: approve_version(s, v, teacher))
    _do(app_engine, v1, activate_version)
    with Session(app_engine) as s:
        assert base_version(s, kb_id).id == v1
    v2 = ready_version_with(app_engine, kb_id, {"a.pdf": "h-a", "b.pdf": "h-b"}, version=2)
    with Session(app_engine) as s:  # the unreviewed draft holds the latest upload
        assert base_version(s, kb_id).id == v2
    _do(app_engine, v2, lambda s, v: approve_version(s, v, teacher))
    _do(app_engine, v2, activate_version)
    _do(app_engine, v1, activate_version)  # rollback: v2 is approved, not a draft
    with Session(app_engine) as s:
        assert base_version(s, kb_id).id == v1


def test_api_approval_flow(app_engine: Engine, course) -> None:
    from app.main import app

    kb_id, teacher_name = course
    ready_version_with(app_engine, kb_id, {"a.pdf": "h-a"})
    http, me = TestClient(app), {"X-CMP-Dev-Teacher": teacher_name}
    url = f"/api/courses/{kb_id}/versions/1"
    other = {"X-CMP-Dev-Teacher": teacher_name + "-other"}

    assert http.post(url + "/approve", headers=other).status_code == 404
    assert http.post(url + "/activate", headers=me).status_code == 409
    r = http.post(url + "/approve", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["approved_at"] is not None
    assert http.delete(url, headers=me).status_code == 409  # approved: kept as history
    r = http.post(url + "/activate", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "active"
    courses = http.get("/api/courses", headers=me).json()
    (course_out,) = [c for c in courses if c["id"] == str(kb_id)]
    assert course_out["active_version"] == 1
