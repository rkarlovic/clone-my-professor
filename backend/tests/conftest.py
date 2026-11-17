import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from core.db import kb_scope
from core.models import Chunk, Document, IngestStatus, KbVersion
from core.settings import get_settings


def _engine_or_skip(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM kb_version LIMIT 0"))
    except OperationalError:
        pytest.skip("dev Postgres not reachable (start infra/docker-compose.dev.yml)")
    except Exception:
        pytest.skip("schema missing (run: uv run alembic upgrade head)")
    return engine


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    return _engine_or_skip(get_settings().database_owner_url)


@pytest.fixture(scope="session")
def app_engine() -> Engine:
    return _engine_or_skip(get_settings().database_url)


@pytest.fixture
def two_kb_versions(owner_engine: Engine) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two knowledge bases (two subjects) with one version each; removed afterwards."""
    teacher, kb_a, kb_b, v_a, v_b = (uuid.uuid4() for _ in range(5))
    with owner_engine.begin() as conn:
        conn.execute(text("INSERT INTO teacher (id, name) VALUES (:t, 'Test')"), {"t": teacher})
        conn.execute(
            text(
                "INSERT INTO knowledge_base (id, teacher_id, subject_name) "
                "VALUES (:a, :t, 'Subject A'), (:b, :t, 'Subject B')"
            ),
            {"a": kb_a, "b": kb_b, "t": teacher},
        )
        conn.execute(
            text("INSERT INTO kb_version (id, kb_id, version) VALUES (:va, :a, 1), (:vb, :b, 1)"),
            {"va": v_a, "vb": v_b, "a": kb_a, "b": kb_b},
        )
    yield v_a, v_b
    with owner_engine.begin() as conn:  # owner is superuser in dev, so RLS does not hide rows
        params = {"va": v_a, "vb": v_b}
        conn.execute(text("DELETE FROM teacher_question WHERE kb_version_id IN (:va, :vb)"), params)
        conn.execute(text("DELETE FROM chunk WHERE kb_version_id IN (:va, :vb)"), params)
        conn.execute(text("DELETE FROM document WHERE kb_version_id IN (:va, :vb)"), params)
        conn.execute(text("DELETE FROM kb_version WHERE id IN (:va, :vb)"), params)
        conn.execute(
            text("DELETE FROM knowledge_base WHERE id IN (:a, :b)"), {"a": kb_a, "b": kb_b}
        )
        conn.execute(text("DELETE FROM teacher WHERE id = :t"), {"t": teacher})


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
        conn.execute(text("DELETE FROM check_question WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM persona_config WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM chat_message WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM teacher_question WHERE kb_id = :k"), p)
        conn.execute(text(f"DELETE FROM chunk WHERE kb_version_id IN ({versions})"), p)
        conn.execute(text(f"DELETE FROM document WHERE kb_version_id IN ({versions})"), p)
        conn.execute(text("UPDATE kb_version SET base_version_id = NULL WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM kb_version WHERE kb_id = :k"), p)
        conn.execute(text("DELETE FROM knowledge_base WHERE id = :k"), p)
        # also the "<name>-other" teachers the API tests create through the dev header
        conn.execute(text("DELETE FROM teacher WHERE name LIKE :n"), {"n": name + "%"})


def ready_version_with(
    app_engine: Engine, kb_id: uuid.UUID, files: dict[str, str], version: int = 1
) -> uuid.UUID:
    """A processed version holding documents {file_name: file_hash}, each with a parent and a
    child chunk."""
    with Session(app_engine) as s, s.begin():
        v = KbVersion(kb_id=kb_id, version=version, ingest_status=IngestStatus.ready)
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
