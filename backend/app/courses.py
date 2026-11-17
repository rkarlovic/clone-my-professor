"""Teacher API: courses (knowledge bases), uploads and versions (build step 3).

An upload never changes what students see: it becomes a new *draft* version (rule 5) that a
background job processes. The teacher then approves it and activates it (students get only the
active version); a draft they do not want can be discarded. See core/approval.py.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeacherDep
from core.approval import (
    ApprovalError,
    activate_version,
    active_version,
    approve_version,
    discard_version,
)
from core.checks import delete_question
from core.db import app_engine, kb_scope
from core.kb import NothingChanged, StillProcessing, create_upload_version
from core.models import CheckQuestion, Document, IngestStatus, KbVersion, KnowledgeBase
from core.storage import UploadError, store_upload
from jobs import enqueue_ingest

router = APIRouter(prefix="/api/courses", tags=["courses"])


class CourseIn(BaseModel):
    subject_name: str = Field(min_length=1, max_length=300)


class CourseOut(BaseModel):
    id: uuid.UUID
    subject_name: str
    active_version: int | None = None  # what students get; None until one is activated


class DocumentOut(BaseModel):
    file_name: str
    file_hash: str


class VersionOut(BaseModel):
    version: int
    status: str  # approval: draft | approved | active
    ingest_status: str  # queued | processing | ready | failed
    ingest_error: str | None
    created_at: datetime
    pending_files: list[str]  # uploaded in this version (processed or still to process)
    origin: str = "upload"  # upload (the teacher's) | lms (found by the material sync)
    approved_at: datetime | None = None
    activated_at: datetime | None = None
    # The background check: queued | running | done | failed (None: not run); its result
    check_status: str | None = None
    check: dict | None = None
    summary: dict | None = None  # from the ingest report, once ready
    documents: list[DocumentOut] | None = None  # only on the detail view, once ready


def _own_course(s: Session, teacher_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
    kb = s.get(KnowledgeBase, kb_id)
    if kb is None or kb.teacher_id != teacher_id:
        raise HTTPException(404, "course not found")  # same answer whether absent or not yours
    return kb


def _version(s: Session, kb_id: uuid.UUID, number: int) -> KbVersion:
    v = s.scalars(
        select(KbVersion).where(KbVersion.kb_id == kb_id, KbVersion.version == number)
    ).first()
    if v is None:
        raise HTTPException(404, "version not found")
    return v


def _summary(report: dict | None) -> dict | None:
    if not report:
        return None
    keys = ("documents", "pages", "chunks_embedded", "carried_over", "sections", "seconds")
    return {k: report.get(k) for k in keys}


def _out(v: KbVersion, documents: list[DocumentOut] | None = None) -> VersionOut:
    return VersionOut(
        version=v.version,
        status=v.status.value,
        ingest_status=v.ingest_status.value,
        ingest_error=v.ingest_error,
        created_at=v.created_at,
        pending_files=[f["file_name"] for f in v.pending_files],
        origin=v.origin,
        approved_at=v.approved_at,
        activated_at=v.activated_at,
        check_status=v.check_status,
        check=v.check_report,
        summary=_summary(v.ingest_report),
        documents=documents,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_course(body: CourseIn, teacher: TeacherDep) -> CourseOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        kb = KnowledgeBase(teacher_id=teacher.id, subject_name=body.subject_name.strip())
        s.add(kb)
    return CourseOut(id=kb.id, subject_name=kb.subject_name)


@router.get("")
def list_courses(teacher: TeacherDep) -> list[CourseOut]:
    with Session(app_engine()) as s:
        kbs = s.scalars(
            select(KnowledgeBase)
            .where(KnowledgeBase.teacher_id == teacher.id)
            .order_by(KnowledgeBase.subject_name)
        ).all()
        out = []
        for k in kbs:
            active = active_version(s, k.id)
            out.append(
                CourseOut(
                    id=k.id,
                    subject_name=k.subject_name,
                    active_version=active.version if active else None,
                )
            )
        return out


@router.get("/{kb_id}/versions")
def list_versions(kb_id: uuid.UUID, teacher: TeacherDep) -> list[VersionOut]:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        versions = s.scalars(
            select(KbVersion).where(KbVersion.kb_id == kb_id).order_by(KbVersion.version.desc())
        ).all()
        return [_out(v) for v in versions]


@router.get("/{kb_id}/versions/{number}")
def get_version(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> VersionOut:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        v = _version(s, kb_id, number)
        documents = None
        if v.ingest_status == IngestStatus.ready:
            with kb_scope(v.id) as ks:
                rows = ks.execute(
                    select(Document.file_name, Document.file_hash).order_by(Document.file_name)
                ).all()
            documents = [DocumentOut(file_name=n, file_hash=h) for n, h in rows]
        return _out(v, documents)


@router.post("/{kb_id}/versions", status_code=status.HTTP_202_ACCEPTED)
def upload(
    kb_id: uuid.UUID,
    teacher: TeacherDep,
    files: Annotated[list[UploadFile], File(description="PDF, PPTX or DOCX files")],
) -> VersionOut:
    """Upload files into a new draft version and queue its processing. Unchanged files
    (same name and content as in the current version) are carried over, not re-processed."""
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
    try:
        stored = [store_upload(kb_id, f.filename or "", f.file) for f in files]
    except UploadError as e:
        raise HTTPException(422, str(e)) from e
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        kb = _own_course(s, teacher.id, kb_id)
        try:
            version = create_upload_version(s, kb, stored)
        except NothingChanged as e:
            raise HTTPException(409, str(e)) from e
        except StillProcessing as e:
            raise HTTPException(409, str(e)) from e
    return _queue(version, kb_id)


@router.post("/{kb_id}/versions/{number}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> VersionOut:
    """Queue a failed version again (same files; the job starts from a clean version)."""
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        _own_course(s, teacher.id, kb_id)
        v = _version(s, kb_id, number)
        if v.ingest_status != IngestStatus.failed:
            raise HTTPException(409, f"version {number} is {v.ingest_status.value}, not failed")
        v.ingest_status, v.ingest_error = IngestStatus.queued, None
    return _queue(v, kb_id)


def _queue(version: KbVersion, kb_id: uuid.UUID) -> VersionOut:
    """Queue after the version is committed (the queue has its own connection). If queueing
    fails, the version says so and can be retried, instead of waiting forever."""
    try:
        enqueue_ingest(version.id, kb_id)
    except Exception as e:
        with Session(app_engine(), expire_on_commit=False) as s, s.begin():
            v = s.get(KbVersion, version.id)
            v.ingest_status = IngestStatus.failed
            v.ingest_error = f"could not queue processing: {type(e).__name__}"
        return _out(v)
    return _out(version)


def _step(kb_id: uuid.UUID, number: int, teacher_id: uuid.UUID, action) -> VersionOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        _own_course(s, teacher_id, kb_id)
        v = _version(s, kb_id, number)
        try:
            action(s, v)
        except ApprovalError as e:
            raise HTTPException(409, str(e)) from e
    return _out(v)


@router.post("/{kb_id}/versions/{number}/approve")
def approve(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> VersionOut:
    """The teacher has reviewed this processed draft (files, sections, summary). Students still
    do not see it until it is activated."""
    return _step(kb_id, number, teacher.id, lambda s, v: approve_version(s, v, teacher.id))


@router.post("/{kb_id}/versions/{number}/activate")
def activate(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> VersionOut:
    """Students get this approved version from now on; the previous active one stays approved
    and can be activated again (rollback)."""
    return _step(kb_id, number, teacher.id, activate_version)


@router.delete("/{kb_id}/versions/{number}", status_code=status.HTTP_204_NO_CONTENT)
def discard(kb_id: uuid.UUID, number: int, teacher: TeacherDep) -> Response:
    """Discard a draft (never an approved or active version)."""
    _step(kb_id, number, teacher.id, discard_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CheckQuestionOut(BaseModel):
    id: uuid.UUID
    question: str
    style: str  # casual | symptom | english | paraphrase | no_diacritics
    file_name: str
    page: int
    created_at: datetime


@router.get("/{kb_id}/checks")
def list_checks(kb_id: uuid.UUID, teacher: TeacherDep) -> list[CheckQuestionOut]:
    """The course's auto-generated check questions (not deleted), by file and slide. A question
    is used for a version only while that version holds the same file content."""
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        rows = s.scalars(
            select(CheckQuestion)
            .where(CheckQuestion.kb_id == kb_id, CheckQuestion.deleted_at.is_(None))
            .order_by(CheckQuestion.file_name, CheckQuestion.page)
        ).all()
        return [
            CheckQuestionOut(
                id=q.id,
                question=q.question,
                style=q.style,
                file_name=q.file_name,
                page=q.page,
                created_at=q.created_at,
            )
            for q in rows
        ]


@router.delete("/{kb_id}/checks/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_check(kb_id: uuid.UUID, question_id: uuid.UUID, teacher: TeacherDep) -> Response:
    """Delete a bad check question; it is not written again for the same file."""
    with Session(app_engine()) as s, s.begin():
        _own_course(s, teacher.id, kb_id)
        if not delete_question(s, kb_id, question_id):
            raise HTTPException(404, "check question not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
