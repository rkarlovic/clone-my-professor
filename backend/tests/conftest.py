import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

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
