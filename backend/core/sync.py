"""Material sync from a linked LMS course (CLAUDE.md "Connectors"): detected changes become a
draft version that the teacher approves (rule 5); nothing goes live by itself.

    sync_course(link):
      1. the connector lists the course's visible files;
      2. each gets a content hash: reused when its name, size and modification time are the
         ones seen last time, otherwise downloaded (into the content-addressed file store);
      3. files whose content is not in the course yet become a new draft version (origin
         "lms"), processed by the same ingest job as uploads.

Platform-independent: the LMS specifics live in connectors/. Files that disappeared from the LMS
are reported but not removed (versions cannot drop files yet).
"""

import io
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from connectors.base import Connector, Material
from core.approval import active_version
from core.db import app_engine, kb_scope
from core.kb import (
    NothingChanged,
    StillProcessing,
    base_version,
    create_removal_version,
    create_upload_version,
)
from core.models import (
    Document,
    KbVersion,
    KbVersionStatus,
    KnowledgeBase,
    LmsFile,
    LtiPlatform,
    PlatformContextMap,
)
from core.settings import get_settings
from core.storage import StoredFile, UploadError, store_upload


class SyncNotConfigured(RuntimeError):
    """The platform has no Web Services address or token."""


def connector_for(platform: LtiPlatform) -> Connector:
    """The connector for a registered platform; the token is read from the environment
    variable the platform row names (secrets are never stored in the database)."""
    token = os.environ.get(platform.ws_token_env or "", "")
    if not platform.ws_base_url or not token:
        raise SyncNotConfigured("Web Services are not set up for this platform")
    from connectors.moodle.client import MoodleClient  # the only kind so far

    return MoodleClient(platform.ws_base_url, token)


def unique_names(materials: list[Material]) -> dict[str, str]:
    """external_id -> the file name used in CMP; a name used twice gets " (2)", " (3)" …"""
    seen: dict[str, int] = {}
    names = {}
    for m in sorted(materials, key=lambda m: m.external_id):
        n = seen.get(m.file_name, 0) + 1
        seen[m.file_name] = n
        stem, dot, ext = m.file_name.rpartition(".")
        names[m.external_id] = m.file_name if n == 1 else f"{stem} ({n}){dot}{ext}"
    return names


def _stored_path(kb_id: uuid.UUID, file_hash: str, name: str) -> Path:
    return get_settings().upload_dir / str(kb_id) / f"{file_hash}{Path(name).suffix.lower()}"


def sync_course(
    map_id: uuid.UUID,
    *,
    connector_factory: Callable[[LtiPlatform], Connector] = connector_for,
    enqueue: Callable[[uuid.UUID, uuid.UUID], object] | None = None,
) -> dict:
    """One sync of a linked course. Returns (and stores on the link) a report with `status`:
    removed (files gone from the LMS left the live version) | new_version | unchanged |
    busy (an earlier version is still processing; next time) | not_configured | failed.
    A removal and new files never happen in one run: removals first, new files next time."""
    from jobs import enqueue_ingest

    enqueue = enqueue or enqueue_ingest
    started = datetime.now(UTC)
    with Session(app_engine()) as s:
        link = s.get(PlatformContextMap, map_id)
        platform = s.get(LtiPlatform, link.platform_id)
        kb_id, course_id = link.kb_id, link.context_id
        known = {
            f.external_id: f for f in s.scalars(select(LmsFile).where(LmsFile.map_id == map_id))
        }
        s.expunge_all()
    report: dict = {"started_at": started.isoformat()}
    try:
        connector = connector_factory(platform)
        materials = connector.list_materials(course_id)
        names = unique_names(materials)
        files: list[StoredFile] = []
        downloaded, skipped = [], []
        for m in materials:
            name = names[m.external_id]
            seen = known.get(m.external_id)
            same = seen and (seen.filesize, seen.timemodified) == (m.size, m.modified)
            path = seen and seen.file_hash and _stored_path(kb_id, seen.file_hash, name)
            if same and path and path.exists():
                files.append(StoredFile(name, seen.file_hash, path, m.size))
                continue
            try:
                stored = store_upload(kb_id, name, _Stream(connector.download(m)))
            except UploadError as e:
                skipped.append(f"{name}: {e}")
                continue
            files.append(stored)
            downloaded.append(name)
            _remember(map_id, m, name, stored.file_hash)
        report |= {"files": len(materials), "downloaded": downloaded, "skipped": skipped}

        _forget_missing(map_id, {m.external_id for m in materials})

        # 1. Files the sync brought in that are gone from every linked LMS course (deleted or
        #    hidden) leave the live version at once, without waiting for the teacher.
        source, gone = _gone_files(kb_id, map_id, set(names.values()))
        if gone and not materials:
            # An empty listing is more likely a permission or network problem than a teacher
            # deleting everything: remove nothing.
            report["removal_skipped"] = "the LMS returned no files; nothing removed"
            gone = set()
        if gone:
            published = source.status == KbVersionStatus.active  # else: a newer draft
            with Session(app_engine(), expire_on_commit=False) as s, s.begin():
                kb = s.get(KnowledgeBase, kb_id)
                version = create_removal_version(
                    s,
                    kb,
                    gone,
                    from_version=s.get(KbVersion, source.id),
                    origin="lms",
                    auto_activate=published,
                )
            enqueue(version.id, kb_id)
            report |= {
                "status": "removed",
                "version": version.version,
                "removed_files": sorted(version.removed_files),
                "published": published,
            }
            _finish(map_id, report)  # new files, if any, come with the next sync
            return report

        # 2. New or changed files become a draft for the teacher.
        with Session(app_engine()) as s:
            base = base_version(s, kb_id)
        in_course: dict[str, str] = {}
        if base:
            with kb_scope(base.id) as ks:
                in_course = dict(ks.execute(select(Document.file_name, Document.file_hash)).all())
        new = [f for f in files if in_course.get(f.file_name) != f.file_hash]
        if not new:
            report["status"] = "unchanged"
        else:
            with Session(app_engine(), expire_on_commit=False) as s, s.begin():
                kb = s.get(KnowledgeBase, kb_id)
                version = create_upload_version(s, kb, new, origin="lms")
            enqueue(version.id, kb_id)
            report |= {
                "status": "new_version",
                "version": version.version,
                "new_files": [f.file_name for f in new],
            }
    except SyncNotConfigured as e:
        report |= {"status": "not_configured", "error": str(e)}
    except NothingChanged:
        report["status"] = "unchanged"
    except StillProcessing as e:
        report |= {"status": "busy", "error": str(e)}
    except Exception as e:  # noqa: BLE001 - recorded for the teacher, then re-raised for the log
        report |= {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]}
        _finish(map_id, report)
        raise
    _finish(map_id, report)
    return report


class _Stream(io.RawIOBase):
    """A file-like reader over a byte-chunk iterator (for store_upload)."""

    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self._buffer) < size:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        if size < 0:
            out, self._buffer = self._buffer, b""
        else:
            out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out


def _gone_files(
    kb_id: uuid.UUID, map_id: uuid.UUID, here: set[str]
) -> tuple[KbVersion | None, set[str]]:
    """Documents that came from an LMS and are in none of the course's linked LMS courses any
    more (not in this course's current listing, nor last seen in another linked course), and the
    version to remove them from: the live version if it has any (published at once), else a
    newer draft that has some (a new draft, so the teacher cannot publish them by approving the
    old one). The teacher's own uploads are never included."""
    with Session(app_engine(), expire_on_commit=False) as s:
        live = active_version(s, kb_id)
        newest = base_version(s, kb_id)
        elsewhere = set(
            s.scalars(
                select(LmsFile.file_name)
                .join(PlatformContextMap, PlatformContextMap.id == LmsFile.map_id)
                .where(PlatformContextMap.kb_id == kb_id, LmsFile.map_id != map_id)
            )
        )
    for version in (live, newest):
        if version is None:
            continue
        gone = {n for n in _lms_documents(version.id) if n not in here and n not in elsewhere}
        if gone:
            return version, gone
    return None, set()


def _forget_missing(map_id: uuid.UUID, present: set[str]) -> None:
    """Drop the remembered files this LMS course no longer lists."""
    with Session(app_engine()) as s, s.begin():
        for row in s.scalars(select(LmsFile).where(LmsFile.map_id == map_id)):
            if row.external_id not in present:
                s.delete(row)


def _lms_documents(version_id: uuid.UUID) -> dict[str, str]:
    with kb_scope(version_id) as s:
        return dict(
            s.execute(
                select(Document.file_name, Document.file_hash).where(
                    Document.source_platform == "lms"
                )
            ).all()
        )


def _remember(map_id: uuid.UUID, m: Material, name: str, file_hash: str) -> None:
    with Session(app_engine()) as s, s.begin():
        row = s.scalars(
            select(LmsFile).where(LmsFile.map_id == map_id, LmsFile.external_id == m.external_id)
        ).first() or LmsFile(map_id=map_id, external_id=m.external_id)
        row.file_name, row.filesize, row.timemodified = name, m.size, m.modified
        row.file_hash, row.seen_at = file_hash, datetime.now(UTC)
        s.add(row)


def _finish(map_id: uuid.UUID, report: dict) -> None:
    with Session(app_engine()) as s, s.begin():
        link = s.get(PlatformContextMap, map_id)
        link.last_sync_at = datetime.now(UTC)
        link.last_sync_status = report.get("status")
        link.last_sync_report = report
