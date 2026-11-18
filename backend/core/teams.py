"""CMP as a Microsoft Teams tab, for schools without LTI in Teams (build step 5).

The tab shows CMP's own pages inside Teams. Who the user is comes from **Teams single sign-on**:
the page asks Teams for a token (`authentication.getAuthToken`), an Entra ID access token for
CMP's app, and sends it with the team's id to /api/teams/session. Here:

    1. the token is verified: signature against Microsoft's key set, issuer = the token's own
       tenant, audience = CMP's app (client id or app id URI), expiry; the tenant must be a
       registered Teams platform (admin page / scripts.lti_platform, kind "teams")
    2. the team id from the page is never trusted on its own: Microsoft Graph (the app's own
       identity, the platform's sync credentials) says whether this user is an **owner** of
       that team (= teacher, as in class teams) or a **member** (= student); anyone else is
       refused
    3. from there it is the LTI flow: a teacher links the team to a CMP course once (or joins
       it if linked), students of a linked team get its chat. The team is the "course", so
       the platform row also does the team's material sync (its context id is the team id).

Rule 9: only sha256("teams:" + tenant, user object id) is kept; names and e-mails in the token
are not read. One Entra app can do all of it: SSO (an exposed API scope, Teams' clients
pre-authorised) and the application permissions for Graph (GroupMember.Read.All to check
membership, Files.Read.All or Sites.Selected for material sync).
"""

import uuid
from dataclasses import dataclass
from urllib.parse import quote

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import lti
from core.models import LtiPlatform, Teacher
from core.settings import get_settings

KEYS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
GRAPH = "https://graph.microsoft.com/v1.0"
LEEWAY_SECONDS = 60


class TeamsAuthError(ValueError):
    """The sign-in is not acceptable; the message is safe to show."""


@dataclass(frozen=True)
class TeamsUser:
    tenant_id: str
    object_id: str
    user_ref: str  # sha256 of tenant + object id: opaque


def app_audiences() -> set[str]:
    s = get_settings()
    if not s.teams_app_client_id:
        raise TeamsAuthError("the Teams app is not set up on this server")
    out = {s.teams_app_client_id}
    if s.teams_app_id_uri:
        out.add(s.teams_app_id_uri)
    return out


def verify_token(token: str) -> TeamsUser:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        claims = jwt.decode(
            token,
            lti.signing_key(KEYS_URL, kid),
            algorithms=["RS256"],
            audience=list(app_audiences()),
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss", "tid", "oid"]},
        )
    except TeamsAuthError:
        raise
    except (jwt.PyJWTError, lti.LtiError) as e:
        raise TeamsAuthError(f"the Teams sign-in token is not valid ({type(e).__name__})") from e
    tid, oid = str(claims["tid"]), str(claims["oid"])
    issuers = {f"https://login.microsoftonline.com/{tid}/v2.0", f"https://sts.windows.net/{tid}/"}
    if claims["iss"] not in issuers:
        raise TeamsAuthError("the Teams sign-in token comes from another issuer")
    return TeamsUser(tid, oid, lti.user_ref(f"teams:{tid}", oid))


def platform_for(session: Session, tenant_id: str) -> LtiPlatform:
    rows = session.scalars(select(LtiPlatform).where(LtiPlatform.kind == "teams")).all()
    for p in rows:
        if (p.sync_config or {}).get("tenant_id") == tenant_id:
            return p
    raise TeamsAuthError("this school's Microsoft tenant is not registered with CMP")


def membership(platform: LtiPlatform, team_id: str, object_id: str) -> str | None:
    """'owner' | 'member' | None, from Microsoft Graph with the platform's app credentials."""
    from core.sync import connector_for  # the same credentials as material sync

    client = connector_for(platform)
    headers = client._bearer()
    http = client.http
    team = quote(team_id)
    url = f"{GRAPH}/groups/{team}/owners?$select=id&$top=999"
    while url:
        r = http.get(url, headers=headers)
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            raise TeamsAuthError("Microsoft Graph refused the membership check (permissions)")
        r.raise_for_status()
        body = r.json()
        if any(o.get("id") == object_id for o in body.get("value", [])):
            return "owner"
        url = body.get("@odata.nextLink", "")
    r = http.post(
        f"{GRAPH}/users/{quote(object_id)}/checkMemberGroups",
        json={"groupIds": [team_id]},
        headers=headers,
    )
    if r.status_code in (401, 403):
        raise TeamsAuthError("Microsoft Graph refused the membership check (permissions)")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return "member" if team_id in r.json().get("value", []) else None


def teacher_for(session: Session, user: TeamsUser) -> Teacher:
    t = session.scalars(select(Teacher).where(Teacher.external_ref == user.user_ref)).first()
    if t is None:
        t = Teacher(name="Nastavnik (Teams)", external_ref=user.user_ref)
        session.add(t)
        session.flush()
    return t


def is_team_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
