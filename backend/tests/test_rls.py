"""Rule 3: subjects never mix — enforced by Postgres RLS, not by query filters."""

import uuid

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from core.db import kb_scope
from core.models import Chunk, Document

pytestmark = pytest.mark.db


def _add_doc_with_chunk(session: Session, kb_version_id: uuid.UUID, body: str) -> None:
    doc = Document(kb_version_id=kb_version_id, file_name=f"{body}.pdf", file_hash="0" * 64)
    session.add(doc)
    session.flush()
    session.add(Chunk(kb_version_id=kb_version_id, document_id=doc.id, text=body))


def test_each_scope_sees_only_its_own_kb_version(app_engine: Engine, two_kb_versions) -> None:
    v_a, v_b = two_kb_versions
    with kb_scope(v_a, app_engine) as s:
        _add_doc_with_chunk(s, v_a, "alpha")
    with kb_scope(v_b, app_engine) as s:
        _add_doc_with_chunk(s, v_b, "beta")

    with kb_scope(v_a, app_engine) as s:
        # No WHERE clause on purpose: isolation must come from the database.
        assert s.scalars(select(Chunk.text)).all() == ["alpha"]
        assert s.scalar(select(func.count()).select_from(Document)) == 1


def test_no_scope_sees_nothing(app_engine: Engine, two_kb_versions) -> None:
    v_a, _ = two_kb_versions
    with kb_scope(v_a, app_engine) as s:
        _add_doc_with_chunk(s, v_a, "alpha")
    with Session(app_engine) as s:
        assert s.scalar(text("SELECT count(*) FROM chunk")) == 0


def test_cannot_write_into_another_kb_version(app_engine: Engine, two_kb_versions) -> None:
    v_a, v_b = two_kb_versions
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        kb_scope(v_a, app_engine) as s,
    ):
        _add_doc_with_chunk(s, v_b, "smuggled")
