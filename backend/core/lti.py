"""LTI 1.3 tool side (build step 4): an LMS (Moodle/Merlin first) launches CMP and says who the
user is and in which course, so nobody needs a CMP password.

    1. login:  the LMS calls /api/lti/login (OIDC third-party-initiated login); we store a
               state + nonce and send the browser to the LMS's auth URL.
    2. launch: the LMS posts a signed id_token to /api/lti/launch; we verify it (signature
               against the LMS's public key set, issuer, audience = our client id, expiry,
               nonce and state single-use, deployment, message type, LTI version) and read the
               user's role and course (context).
    3. CMP:    a teacher links the LMS course to a CMP course once; students of a linked course
               get its chat. Both receive a session token (core/sessions.py).

Rule 9: only an opaque sha256 of (issuer, LMS user id) is kept; names, e-mails and the LMS id
itself are never stored. Thin translator only: no course logic here (CLAUDE.md "Connectors").
"""

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import KnowledgeBase, LtiLoginState, LtiPlatform, PlatformContextMap, Teacher
from core.settings import get_settings

CLAIM = "https://purl.imsglobal.org/spec/lti/claim/"
TEACHER_ROLES = (
    "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
    "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
    "http://purl.imsglobal.org/vocab/lis/v2/membership#Administrator",
)
STATE_MINUTES = 10
LEEWAY_SECONDS = 60
JWKS_CACHE_SECONDS = 600


class LtiError(ValueError):
    """The login or launch is not acceptable; the message is safe to show."""


# --- the tool's own key ------------------------------------------------------------------


@lru_cache
def tool_private_key() -> rsa.RSAPrivateKey:
    path = get_settings().lti_tool_key_file
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def tool_jwks() -> dict:
    """The tool's public key set (the LMS needs its URL to register the tool)."""
    public = tool_private_key().public_key()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public))
    der = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    jwk |= {"kid": hashlib.sha256(der).hexdigest()[:16], "alg": "RS256", "use": "sig"}
    return {"keys": [jwk]}


def tool_urls() -> dict[str, str]:
    """What an LMS administrator enters when registering CMP as an external tool."""
    base = get_settings().public_url.rstrip("/")
    return {
        "tool_url": f"{base}/api/lti/launch",
        "initiate_login_url": f"{base}/api/lti/login",
        "redirection_uris": f"{base}/api/lti/launch",
        "public_keyset_url": f"{base}/api/lti/jwks",
    }


# --- 1. login ------------------------------------------------------------------------------


def find_platform(session: Session, issuer: str, client_id: str | None) -> LtiPlatform:
    q = select(LtiPlatform).where(LtiPlatform.issuer == issuer)
    if client_id:
        q = q.where(LtiPlatform.client_id == client_id)
    found = session.scalars(q).all()
    if len(found) != 1:
        raise LtiError("this learning platform is not registered with CMP")
    return found[0]


def begin_login(session: Session, params: dict[str, str]) -> str:
    """Store a state + nonce and return the LMS auth URL to redirect the browser to."""
    for required in ("iss", "login_hint", "target_link_uri"):
        if not params.get(required):
            raise LtiError(f"login request without {required}")
    urls = tool_urls()
    if params["target_link_uri"] != urls["tool_url"]:
        raise LtiError("unexpected target_link_uri")  # never redirect anywhere else
    platform = find_platform(session, params["iss"], params.get("client_id"))
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(LtiLoginState(state=state, nonce=nonce, platform_id=platform.id))
    query = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": platform.client_id,
        "redirect_uri": urls["tool_url"],
        "login_hint": params["login_hint"],
        "state": state,
        "nonce": nonce,
    }
    if params.get("lti_message_hint"):
        query["lti_message_hint"] = params["lti_message_hint"]
    return f"{platform.auth_login_url}?{urlencode(query)}"


# --- 2. launch -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Launch:
    platform_id: uuid.UUID
    issuer: str
    user_ref: str  # sha256(issuer, sub): opaque
    is_teacher: bool
    context_id: str
    context_title: str | None


_jwks_cache: dict[str, tuple[float, dict]] = {}


def _platform_keys(url: str, refresh: bool = False) -> dict:
    cached = _jwks_cache.get(url)
    if cached and not refresh and time.time() - cached[0] < JWKS_CACHE_SECONDS:
        return cached[1]
    keys = httpx.get(url, timeout=10).raise_for_status().json()
    _jwks_cache[url] = (time.time(), keys)
    return keys


def _signing_key(platform: LtiPlatform, kid: str | None):
    for refresh in (False, True):  # a new key id: fetch the set again once
        for jwk in _platform_keys(platform.jwks_url, refresh).get("keys", []):
            if kid is None or jwk.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    raise LtiError("the platform's signing key was not found")


def user_ref(issuer: str, sub: str) -> str:
    return hashlib.sha256(f"{issuer}\n{sub}".encode()).hexdigest()


def complete_launch(session: Session, id_token: str, state: str) -> Launch:
    """Verify the launch; the state is consumed even when the launch is refused."""
    row = session.get(LtiLoginState, state, with_for_update=True)
    if row is None or row.used_at is not None:
        raise LtiError("this launch was already used or never started; open CMP again")
    row.used_at = datetime.now(UTC)
    if row.created_at < datetime.now(UTC) - timedelta(minutes=STATE_MINUTES):
        raise LtiError("the launch took too long; open CMP again")
    platform = session.get(LtiPlatform, row.platform_id)
    try:
        header = jwt.get_unverified_header(id_token)
        claims = jwt.decode(
            id_token,
            _signing_key(platform, header.get("kid")),
            algorithms=["RS256"],
            audience=platform.client_id,
            issuer=platform.issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "nonce", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise LtiError(f"the launch token is not valid ({type(e).__name__})") from e
    if not secrets.compare_digest(str(claims.get("nonce")), row.nonce):
        raise LtiError("the launch token does not belong to this login")
    several_audiences = isinstance(claims["aud"], list) and len(claims["aud"]) > 1
    if several_audiences and claims.get("azp") != platform.client_id:
        raise LtiError("the launch token is for another tool")
    deployment = claims.get(CLAIM + "deployment_id")
    if not deployment or (platform.deployment_ids and deployment not in platform.deployment_ids):
        raise LtiError("this deployment of CMP is not registered")
    if claims.get(CLAIM + "message_type") != "LtiResourceLinkRequest":
        raise LtiError("only course links are supported")
    if claims.get(CLAIM + "version") != "1.3.0":
        raise LtiError("only LTI 1.3 is supported")
    context = claims.get(CLAIM + "context") or {}
    if not context.get("id"):
        raise LtiError("the launch does not name a course")
    roles = claims.get(CLAIM + "roles") or []
    return Launch(
        platform_id=platform.id,
        issuer=platform.issuer,
        user_ref=user_ref(platform.issuer, claims["sub"]),
        is_teacher=any(r in TEACHER_ROLES for r in roles),
        context_id=str(context["id"]),
        context_title=context.get("title"),
    )


# --- 3. CMP side ---------------------------------------------------------------------------


def teacher_for(session: Session, launch: Launch) -> Teacher:
    """The CMP teacher behind an LMS teacher (created on the first launch, no personal data)."""
    q = select(Teacher).where(Teacher.external_ref == launch.user_ref)
    teacher = session.scalars(q).first()
    if teacher is None:
        teacher = Teacher(name="Nastavnik (LMS)", external_ref=launch.user_ref)
        session.add(teacher)
        session.flush()
    return teacher


def linked_course(session: Session, platform_id: uuid.UUID, context_id: str) -> uuid.UUID | None:
    return session.scalar(
        select(PlatformContextMap.kb_id).where(
            PlatformContextMap.platform_id == platform_id,
            PlatformContextMap.context_id == context_id,
        )
    )


def link_course(
    session: Session,
    teacher_id: uuid.UUID,
    platform_id: uuid.UUID,
    context_id: str,
    context_title: str | None,
    kb_id: uuid.UUID,
) -> PlatformContextMap:
    """Link an LMS course to one of the teacher's CMP courses (many LMS courses -> one KB)."""
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or kb.teacher_id != teacher_id:
        raise LtiError("that CMP course is not yours")
    if linked_course(session, platform_id, context_id):
        raise LtiError("this LMS course is already linked")
    row = PlatformContextMap(
        platform_id=platform_id, context_id=context_id, context_title=context_title, kb_id=kb_id
    )
    session.add(row)
    session.flush()
    return row
