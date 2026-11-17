"""Teacher API: the LMS courses linked to a CMP course and their material sync.

    GET  /api/courses/{kb_id}/lms                     linked LMS courses, sync settings, last result
    PUT  /api/courses/{kb_id}/lms/{link_id}           {"sync_enabled": bool}
    POST /api/courses/{kb_id}/lms/{link_id}/sync      check the LMS course now

Sync is off until the teacher turns it on ("always ask me" stays the rule: changes become a
draft version to review, never a live one).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeacherDep
from app.courses import _own_course
from core.db import app_engine
from core.models import LmsFile, LtiPlatform, PlatformContextMap
from jobs import enqueue_sync

router = APIRouter(prefix="/api/courses", tags=["lms"])


class LmsLinkOut(BaseModel):
    id: uuid.UUID
    platform: str
    context_title: str | None
    sync_available: bool  # the platform has Web Services set up
    sync_enabled: bool
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_report: dict | None
    auto_add_sections: list[str]  # new files from these sections are published without review
    sections: list[str]  # sections seen at the last sync (to choose from)


class SyncSettingsIn(BaseModel):
    sync_enabled: bool
    auto_add_sections: list[str] | None = None  # None: unchanged


def _out(link: PlatformContextMap, platform: LtiPlatform, sections: list[str]) -> LmsLinkOut:
    return LmsLinkOut(
        auto_add_sections=list(link.auto_add_sections or []),
        sections=sections,
        id=link.id,
        platform=platform.name,
        context_title=link.context_title,
        sync_available=bool(platform.ws_base_url and platform.ws_token_env),
        sync_enabled=link.sync_enabled,
        last_sync_at=link.last_sync_at,
        last_sync_status=link.last_sync_status,
        last_sync_report=link.last_sync_report,
    )


def _own_link(s: Session, teacher_id: uuid.UUID, kb_id: uuid.UUID, link_id: uuid.UUID):
    _own_course(s, teacher_id, kb_id)
    link = s.get(PlatformContextMap, link_id)
    if link is None or link.kb_id != kb_id:
        raise HTTPException(404, "link not found")
    return link, s.get(LtiPlatform, link.platform_id)


@router.get("/{kb_id}/lms")
def links(kb_id: uuid.UUID, teacher: TeacherDep) -> list[LmsLinkOut]:
    with Session(app_engine()) as s:
        _own_course(s, teacher.id, kb_id)
        rows = s.execute(
            select(PlatformContextMap, LtiPlatform)
            .join(LtiPlatform, LtiPlatform.id == PlatformContextMap.platform_id)
            .where(PlatformContextMap.kb_id == kb_id)
            .order_by(PlatformContextMap.created_at)
        ).all()
        return [_out(link, platform, _sections(s, link.id)) for link, platform in rows]


@router.put("/{kb_id}/lms/{link_id}")
def settings(
    kb_id: uuid.UUID, link_id: uuid.UUID, body: SyncSettingsIn, teacher: TeacherDep
) -> LmsLinkOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        link, platform = _own_link(s, teacher.id, kb_id, link_id)
        if body.sync_enabled and not (platform.ws_base_url and platform.ws_token_env):
            raise HTTPException(409, "material sync is not set up for this platform")
        link.sync_enabled = body.sync_enabled
        if body.auto_add_sections is not None:
            link.auto_add_sections = sorted(
                {x.strip() for x in body.auto_add_sections if x.strip()}
            )
        sections = _sections(s, link.id)
    return _out(link, platform, sections)


@router.post("/{kb_id}/lms/{link_id}/sync", status_code=202)
def sync_now(kb_id: uuid.UUID, link_id: uuid.UUID, teacher: TeacherDep) -> dict:
    with Session(app_engine()) as s:
        _, platform = _own_link(s, teacher.id, kb_id, link_id)
        if not (platform.ws_base_url and platform.ws_token_env):
            raise HTTPException(409, "material sync is not set up for this platform")
    queued = enqueue_sync(link_id, kb_id)
    return {"queued": queued is not None}


def _sections(s: Session, link_id: uuid.UUID) -> list[str]:
    return sorted(set(s.scalars(select(LmsFile.section).where(LmsFile.map_id == link_id))) - {""})
