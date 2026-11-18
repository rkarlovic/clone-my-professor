"""Teacher sign-in with Microsoft (Entra ID) or Google for CMP's own web app (build step 4).

OpenID Connect authorization-code flow with PKCE, all on the server:

    1. /api/sso/<provider>/login     store state + nonce + PKCE verifier, send the browser to the
                                     provider's sign-in page
    2. /api/sso/<provider>/callback  exchange the code (client secret + verifier) for an id_token,
                                     verify it (signature against the provider's key set, issuer,
                                     audience, expiry, nonce; state single-use), check that the
                                     account belongs to the institution, issue a CMP session

Only institution accounts may sign in: Microsoft needs a specific tenant (`SSO_MICROSOFT_TENANT`)
or `SSO_ALLOWED_DOMAINS`; Google needs `SSO_GOOGLE_HOSTED_DOMAIN` (Workspace) or
`SSO_ALLOWED_DOMAINS`. A provider without such a limit is not offered at all.

Teachers only: students reach CMP through their learning platform (LTI), which says which
course they are in. Rule 9: CMP keeps sha256(issuer, subject) and the display name (teachers
see each other on a shared course); the e-mail is read for the domain check and not stored.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import lti
from core.models import SsoLoginState, Teacher
from core.settings import get_settings

STATE_MINUTES = 10
LEEWAY_SECONDS = 60
DISCOVERY_CACHE_SECONDS = 3600
MULTI_TENANT = {"organizations", "common"}


class SsoError(ValueError):
    """The sign-in is not acceptable; the message is safe to show."""


@dataclass(frozen=True)
class Provider:
    id: str  # "microsoft" | "google"
    label: str
    client_id: str
    client_secret: str
    discovery_url: str
    tenant: str | None = None  # Microsoft
    hosted_domain: str | None = None  # Google Workspace domain


def _domains() -> set[str]:
    raw = get_settings().sso_allowed_domains or ""
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def providers() -> dict[str, Provider]:
    """The configured providers that are limited to the institution."""
    s = get_settings()
    out: dict[str, Provider] = {}
    if s.sso_microsoft_client_id and s.sso_microsoft_client_secret:
        tenant = (s.sso_microsoft_tenant or "organizations").strip()
        if tenant not in MULTI_TENANT or _domains():
            out["microsoft"] = Provider(
                "microsoft",
                "Microsoft",
                s.sso_microsoft_client_id,
                s.sso_microsoft_client_secret,
                f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
                tenant=tenant,
            )
    google_limited = s.sso_google_hosted_domain or _domains()
    if s.sso_google_client_id and s.sso_google_client_secret and google_limited:
        out["google"] = Provider(
            "google",
            "Google",
            s.sso_google_client_id,
            s.sso_google_client_secret,
            "https://accounts.google.com/.well-known/openid-configuration",
            hosted_domain=(s.sso_google_hosted_domain or "").strip().lower() or None,
        )
    return out


def provider(provider_id: str) -> Provider:
    p = providers().get(provider_id)
    if p is None:
        raise SsoError("this sign-in is not set up on this server")
    return p


_discovery_cache: dict[str, tuple[float, dict]] = {}


def discovery(p: Provider) -> dict:
    cached = _discovery_cache.get(p.discovery_url)
    if cached and time.time() - cached[0] < DISCOVERY_CACHE_SECONDS:
        return cached[1]
    doc = httpx.get(p.discovery_url, timeout=10).raise_for_status().json()
    _discovery_cache[p.discovery_url] = (time.time(), doc)
    return doc


def redirect_uri(provider_id: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/sso/{provider_id}/callback"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def begin(session: Session, p: Provider, next_page: str = "teacher") -> str:
    """Store a login round trip; return the provider URL to send the browser to. `next_page`
    (already checked by the caller) is where the pages continue after signing in."""
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    session.add(
        SsoLoginState(
            state=state, nonce=nonce, code_verifier=verifier, provider=p.id, next_page=next_page
        )
    )
    params = {
        "client_id": p.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(p.id),
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if p.hosted_domain:
        params["hd"] = p.hosted_domain  # only a hint for Google's page; checked again below
    return f"{discovery(p)['authorization_endpoint']}?{urlencode(params)}"


@dataclass(frozen=True)
class SignIn:
    user_ref: str  # sha256(issuer, subject): opaque
    name: str


def _issuer_ok(p: Provider, doc: dict, claims: dict) -> bool:
    iss = claims.get("iss")
    if p.id == "microsoft" and p.tenant in MULTI_TENANT:
        # the multi-tenant document names "{tenantid}"; the token carries the real tenant
        tid = claims.get("tid")
        return bool(tid) and iss == doc["issuer"].replace("{tenantid}", str(tid))
    if p.id == "google":
        return iss in ("https://accounts.google.com", "accounts.google.com")
    return iss == doc["issuer"]


def _account_ok(p: Provider, claims: dict) -> bool:
    email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
    domain = email.rpartition("@")[2] if "@" in email else ""
    if p.id == "google":
        if claims.get("email_verified") is not True:
            return False
        if p.hosted_domain and claims.get("hd", "").lower() != p.hosted_domain:
            return False
    domains = _domains()
    return not domains or domain in domains


def complete(session: Session, provider_id: str, code: str, state: str) -> tuple[SignIn, str]:
    """Verify the callback; return who signed in and the page to continue to. The state is
    consumed even when the sign-in is refused."""
    row = session.get(SsoLoginState, state, with_for_update=True)
    if row is None or row.used_at is not None or row.provider != provider_id:
        raise SsoError("this sign-in was already used or never started; sign in again")
    row.used_at = datetime.now(UTC)
    if row.created_at < datetime.now(UTC) - timedelta(minutes=STATE_MINUTES):
        raise SsoError("the sign-in took too long; sign in again")
    p = provider(provider_id)
    doc = discovery(p)
    try:
        r = httpx.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(p.id),
                "client_id": p.client_id,
                "client_secret": p.client_secret,
                "code_verifier": row.code_verifier,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        id_token = r.json()["id_token"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise SsoError("the sign-in could not be completed with the provider") from e
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
        claims = jwt.decode(
            id_token,
            lti.signing_key(doc["jwks_uri"], kid),
            algorithms=["RS256"],
            audience=p.client_id,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "nonce", "aud", "iss"]},
        )
    except (jwt.PyJWTError, lti.LtiError) as e:
        raise SsoError(f"the sign-in token is not valid ({type(e).__name__})") from e
    if not _issuer_ok(p, doc, claims):
        raise SsoError("the sign-in token comes from another issuer")
    if not secrets.compare_digest(str(claims.get("nonce")), row.nonce):
        raise SsoError("the sign-in token does not belong to this sign-in")
    if not _account_ok(p, claims):
        raise SsoError("only accounts of this institution can sign in")
    name = str(claims.get("name") or "").strip()[:200] or "Nastavnik"
    ref = lti.user_ref(f"sso:{claims['iss']}", claims["sub"])
    return SignIn(user_ref=ref, name=name), row.next_page


def teacher_for(session: Session, sign_in: SignIn) -> Teacher:
    """The CMP teacher behind an SSO account (created on the first sign-in)."""
    teacher = session.scalars(
        select(Teacher).where(Teacher.external_ref == sign_in.user_ref)
    ).first()
    if teacher is None:
        teacher = Teacher(name=sign_in.name, external_ref=sign_in.user_ref)
        session.add(teacher)
        session.flush()
    elif teacher.name != sign_in.name:
        teacher.name = sign_in.name  # the institution's directory is the source of the name
    return teacher
