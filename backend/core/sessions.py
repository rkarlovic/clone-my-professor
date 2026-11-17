"""Session tokens: short-lived, signed (HS256) bearer tokens the web pages send as
`Authorization: Bearer …` after an LTI launch (later also after SSO).

Claims kept minimal (rule 9): `sub` is an opaque user reference (never an LMS id, name or
e-mail), `role` is "teacher" or "student", `teacher_id` for teachers, `kbs` the courses a
student may use (from the LTI course link), `link` a pending LMS course a teacher still has to
link. Tokens live in the browser tab's memory/sessionStorage, not in cookies: LMS iframes block
third-party cookies.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt

from core.settings import get_settings

ALGORITHM = "HS256"
AUDIENCE = "cmp-session"


class SessionError(ValueError):
    """The token is missing, malformed, expired or not ours."""


@lru_cache
def _secret() -> str:
    settings = get_settings()
    if settings.session_secret:
        if len(settings.session_secret) < 32:
            raise RuntimeError("SESSION_SECRET must be at least 32 characters")
        return settings.session_secret
    if settings.auth_mode != "dev":
        raise RuntimeError("SESSION_SECRET is not set")
    return secrets.token_urlsafe(48)  # dev only: sessions end when the process restarts


def issue(
    sub: str,
    role: str,
    *,
    teacher_id: uuid.UUID | None = None,
    kbs: list[uuid.UUID] | None = None,
    link: dict | None = None,
    hours: int | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict = {
        "sub": sub,
        "role": role,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=hours or get_settings().session_hours),
    }
    if teacher_id:
        claims["teacher_id"] = str(teacher_id)
    if kbs is not None:
        claims["kbs"] = [str(k) for k in kbs]
    if link:
        claims["link"] = link
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def verify(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "role", "exp"]},
        )
    except jwt.PyJWTError as e:
        raise SessionError(str(e)) from e
