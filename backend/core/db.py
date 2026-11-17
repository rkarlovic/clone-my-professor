"""Database access. Every read/write of KB content goes through `kb_scope`, which pins the
transaction to one kb_version so Postgres row-level security enforces subject separation."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from core.settings import get_settings


@lru_cache
def app_engine() -> Engine:
    """Engine for the application role (RLS applies)."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def kb_scope(kb_version_id: uuid.UUID, engine: Engine | None = None) -> Iterator[Session]:
    """Open a transaction in which only rows of `kb_version_id` are visible or writable."""
    with Session(engine or app_engine()) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.kb_version_id', :v, true)"),
            {"v": str(kb_version_id)},
        )
        yield session


def scope_transaction(session: Session, kb_version_id: uuid.UUID) -> None:
    """Pin the session's *current* transaction to one kb_version (like `kb_scope`), for work
    that must change document/chunk rows and kb_version rows atomically."""
    session.execute(
        text("SELECT set_config('app.kb_version_id', :v, true)"), {"v": str(kb_version_id)}
    )
