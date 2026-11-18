"""The Teams tab's sign-in: a Teams SSO token (simulated Entra ID key) + the team id; Graph says
owner (teacher) or member (student); then the same linking and chat access as an LTI launch."""

import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from connectors.teams.client import TeamsClient
from core import lti, teams
from core.models import LtiPlatform
from core.settings import get_settings

pytestmark = pytest.mark.db
TENANT = str(uuid.uuid4())
CLIENT = str(uuid.uuid4())
TEAM = str(uuid.uuid4())


@pytest.fixture
def tenant(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "e1"}
    monkeypatch.setattr(lti, "_platform_keys", lambda url, refresh=False: {"keys": [jwk]})
    monkeypatch.setattr(get_settings(), "teams_app_client_id", CLIENT)
    roles: dict[str, str] = {}  # object id -> owner | member
    monkeypatch.setattr(
        teams, "membership", lambda p, team, oid: roles.get(oid) if team == TEAM else None
    )
    with Session(owner_engine) as s, s.begin():
        p = LtiPlatform(
            name="Teams (test)",
            issuer=f"https://login.microsoftonline.com/{TENANT}/v2.0",
            client_id=CLIENT,
            auth_login_url="-",
            jwks_url="-",
            kind="teams",
            sync_config={"tenant_id": TENANT, "client_id": CLIENT},
            ws_token_env="CMP_TEAMS_TEST_SECRET",
        )
        s.add(p)
        s.flush()
        pid = p.id
    refs: list[str] = []
    yield {"key": key, "roles": roles, "refs": refs}
    with owner_engine.begin() as c:
        kbs = [
            r[0]
            for r in c.execute(
                text("SELECT kb_id FROM platform_context_map WHERE platform_id = :p"), {"p": pid}
            )
        ]
        c.execute(text("DELETE FROM platform_context_map WHERE platform_id = :p"), {"p": pid})
        for kb in kbs:
            c.execute(text("DELETE FROM course_teacher WHERE kb_id = :k"), {"k": kb})
            c.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb})
        for ref in refs:
            c.execute(
                text(
                    "DELETE FROM course_teacher WHERE teacher_id IN "
                    "(SELECT id FROM teacher WHERE external_ref = :r)"
                ),
                {"r": ref},
            )
            c.execute(text("DELETE FROM teacher WHERE external_ref = :r"), {"r": ref})
        c.execute(text("DELETE FROM lti_platform WHERE id = :p"), {"p": pid})


def _token(t, oid: str, role: str | None = None, **over) -> str:
    if role:
        t["roles"][oid] = role
    t["refs"].append(lti.user_ref(f"teams:{TENANT}", oid))
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": oid,
        "iat": now,
        "exp": now + 600,
        "name": "Ana Anić",  # never read
        "preferred_username": "ana@school.test",
    } | over
    return jwt.encode(claims, t["key"], algorithm="RS256", headers={"kid": "e1"})


@pytest.fixture
def http():
    from app.main import app

    return TestClient(app)


def _session(http, token: str, team: str = TEAM):
    return http.post(
        "/api/teams/session", json={"token": token, "team_id": team, "team_name": "1.A Biologija"}
    )


def test_owner_links_the_team_then_members_get_its_chat(http, tenant) -> None:
    r = _session(http, _token(tenant, "owner-1", "owner"))
    assert r.status_code == 200 and r.json()["next"] == "link"
    teacher = {"Authorization": f"Bearer {r.json()['token']}"}
    assert http.get("/api/lti/link", headers=teacher).json()["context_title"] == "1.A Biologija"
    linked = http.post(
        "/api/lti/link", json={"new_course_name": "Biologija"}, headers=teacher
    ).json()
    kb_id = linked["kb_id"]

    # a member (student) now lands in that course's chat, and only there
    r = _session(http, _token(tenant, "student-1", "member"))
    assert r.json()["next"] == f"chat/{kb_id}"
    student = {"Authorization": f"Bearer {r.json()['token']}"}
    assert http.get(f"/api/chat/{kb_id}", headers=student).status_code == 200
    assert http.get(f"/api/chat/{uuid.uuid4()}", headers=student).status_code == 403

    # a second owner joins the linked course directly
    r = _session(http, _token(tenant, "owner-2", "owner"))
    assert r.json()["next"] == f"teacher/{kb_id}"
    other = {"Authorization": f"Bearer {r.json()['token']}"}
    names = [
        m["added_via"] for m in http.get(f"/api/courses/{kb_id}/teachers", headers=other).json()
    ]
    assert names == ["owner", "teams"]


def test_refusals(http, tenant) -> None:
    assert _session(http, _token(tenant, "nobody")).status_code == 403  # not in the team
    r = _session(http, _token(tenant, "owner-x", "owner"), team=str(uuid.uuid4()))
    assert r.status_code == 403  # another team: Graph says no
    assert _session(http, _token(tenant, "o", "owner", aud="someone-else")).status_code == 403
    assert (
        _session(http, _token(tenant, "o", "owner", exp=int(time.time()) - 3600)).status_code == 403
    )
    other_tenant = str(uuid.uuid4())
    bad = _token(
        tenant,
        "o",
        "owner",
        tid=other_tenant,
        iss=f"https://login.microsoftonline.com/{other_tenant}/v2.0",
    )
    r = _session(http, bad)
    assert r.status_code == 403 and "not registered" in r.json()["detail"]
    wrong_iss = _token(tenant, "o", "owner", iss="https://evil.test/v2.0")
    assert _session(http, wrong_iss).status_code == 403
    assert _session(http, _token(tenant, "o", "owner"), team="not-a-team").status_code == 422
    # an unlinked team's student gets no session
    r = _session(http, _token(tenant, "student-9", "member"))
    assert r.json() == {"token": None, "next": "not-linked"}


def test_graph_membership(monkeypatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if req.url.path.endswith("/owners"):
            return httpx.Response(200, json={"value": [{"id": "boss"}]})
        if req.url.path.endswith("/checkMemberGroups"):
            member = req.url.path.split("/")[-2] == "pupil"
            return httpx.Response(200, json={"value": [TEAM] if member else []})
        return httpx.Response(404)

    client = TeamsClient("t", "a", "s", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("core.sync.connector_for", lambda p: client)
    p = LtiPlatform(kind="teams")
    assert teams.membership(p, TEAM, "boss") == "owner"
    assert teams.membership(p, TEAM, "pupil") == "member"
    assert teams.membership(p, TEAM, "stranger") is None
