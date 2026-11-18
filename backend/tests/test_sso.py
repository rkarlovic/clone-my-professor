"""Teacher sign-in with Microsoft/Google against a simulated provider (its own RSA key), and the
course team: invites, removing, owners."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from core import lti, sso
from core.settings import get_settings

pytestmark = pytest.mark.db
TENANT = "11111111-2222-3333-4444-555555555555"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
CLIENT = "cmp-test-client"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, owner_engine: Engine):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = {
        "keys": [jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "k1"}]
    }
    p = sso.Provider(
        "microsoft", "Microsoft", CLIENT, "secret", "https://idp.test/disc", tenant=TENANT
    )
    doc = {
        "issuer": ISSUER,
        "authorization_endpoint": "https://idp.test/authorize",
        "token_endpoint": "https://idp.test/token",
        "jwks_uri": "https://idp.test/keys",
    }
    monkeypatch.setattr(sso, "providers", lambda: {"microsoft": p})
    monkeypatch.setattr(sso, "discovery", lambda _p: doc)
    monkeypatch.setattr(lti, "_platform_keys", lambda url, refresh=False: jwks)
    sent: dict = {}

    def token_endpoint(url, data, **kw):
        sent.update(data)
        claims = sent["claims"]
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
        return httpx.Response(200, json={"id_token": token}, request=httpx.Request("POST", url))

    monkeypatch.setattr(sso.httpx, "post", token_endpoint)
    refs: list[str] = []
    yield {"sent": sent, "refs": refs}
    with owner_engine.begin() as c:
        for ref in refs:
            mine = "SELECT id FROM teacher WHERE external_ref = :r"
            kbs = f"SELECT id FROM knowledge_base WHERE teacher_id IN ({mine})"
            for table in ("course_invite", "course_teacher"):
                c.execute(text(f"DELETE FROM {table} WHERE kb_id IN ({kbs})"), {"r": ref})
            c.execute(text(f"DELETE FROM course_teacher WHERE teacher_id IN ({mine})"), {"r": ref})
            c.execute(text(f"DELETE FROM knowledge_base WHERE teacher_id IN ({mine})"), {"r": ref})
            c.execute(text("DELETE FROM teacher WHERE external_ref = :r"), {"r": ref})


@pytest.fixture
def http():
    from app.main import app

    return TestClient(app, follow_redirects=False)


def _sign_in(http, provider, sub: str, name: str = "Ana Anić", **over) -> tuple[int, dict]:
    """The whole round trip; returns the status and the fragment's query (token, next)."""
    r = http.get("/api/sso/microsoft/login", params={"next": over.pop("next", "teacher")})
    assert r.status_code == 302, r.text
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].endswith("/callback")
    now = int(time.time())
    provider["sent"]["claims"] = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": sub,
        "tid": TENANT,
        "iat": now,
        "exp": now + 300,
        "nonce": q["nonce"],
        "name": name,
        "email": "ana@uni.test",  # read for the domain check, never stored
    } | over
    provider["refs"].append(lti.user_ref(f"sso:{ISSUER}", sub))
    r = http.get("/api/sso/microsoft/callback", params={"code": "c1", "state": q["state"]})
    if r.status_code != 303:
        return r.status_code, {"body": r.text}
    # PKCE: the verifier sent to the token endpoint matches the challenge in the login URL
    verifier = provider["sent"]["code_verifier"]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert digest.decode() == q["code_challenge"]
    frag = urlparse(r.headers["location"]).fragment
    return 303, {k: v[0] for k, v in parse_qs(frag.split("?", 1)[1]).items()}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_providers_are_offered_only_when_limited_to_the_institution(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "sso_microsoft_client_id", "id")
    monkeypatch.setattr(s, "sso_microsoft_client_secret", "secret")
    monkeypatch.setattr(s, "sso_google_client_id", "id")
    monkeypatch.setattr(s, "sso_google_client_secret", "secret")
    monkeypatch.setattr(s, "sso_allowed_domains", None)
    monkeypatch.setattr(s, "sso_google_hosted_domain", None)
    monkeypatch.setattr(s, "sso_microsoft_tenant", "organizations")
    assert sso.providers() == {}  # any work account / any Google account: not offered
    monkeypatch.setattr(s, "sso_microsoft_tenant", TENANT)
    monkeypatch.setattr(s, "sso_google_hosted_domain", "uni.test")
    assert set(sso.providers()) == {"microsoft", "google"}
    monkeypatch.setattr(s, "sso_microsoft_tenant", "common")
    monkeypatch.setattr(s, "sso_allowed_domains", "uni.test, @other.test")
    assert set(sso.providers()) == {"microsoft", "google"}
    assert sso._domains() == {"uni.test", "other.test"}


def test_sign_in_creates_a_teacher_without_personal_data(http, provider, owner_engine) -> None:
    status, out = _sign_in(http, provider, "sub-a1")
    assert status == 303 and out["next"] == "teacher"
    r = http.post("/api/courses", json={"subject_name": "SSO predmet"}, headers=_auth(out["token"]))
    assert r.status_code == 201
    with owner_engine.connect() as c:
        row = c.execute(
            text("SELECT name, external_ref FROM teacher WHERE external_ref = :r"),
            {"r": provider["refs"][-1]},
        ).one()
    assert row.name == "Ana Anić" and "sub-a1" not in row.external_ref
    # the same account again: the same teacher, who sees their course
    status, again = _sign_in(http, provider, "sub-a1")
    courses = http.get("/api/courses", headers=_auth(again["token"])).json()
    assert [c["subject_name"] for c in courses] == ["SSO predmet"]


@pytest.mark.parametrize(
    "change,expect",
    [
        ({"aud": "someone-else"}, "not valid"),
        ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "another issuer"),
        ({"nonce": "foreign"}, "does not belong"),
        ({"exp": int(time.time()) - 3600}, "not valid"),
    ],
)
def test_bad_tokens_are_refused(http, provider, change, expect) -> None:
    status, out = _sign_in(http, provider, "sub-bad", **change)
    assert status == 400 and expect in out["body"]


def test_only_allowed_domains_and_single_use_states(http, provider, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "sso_allowed_domains", "other.test")
    status, out = _sign_in(http, provider, "sub-dom")
    assert status == 400 and "only accounts of this institution" in out["body"]
    monkeypatch.setattr(get_settings(), "sso_allowed_domains", None)
    r = http.get("/api/sso/microsoft/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    provider["sent"]["claims"] = {}  # never reached: the state is checked first
    assert (
        http.get("/api/sso/microsoft/callback", params={"code": "x", "state": "nope"}).status_code
        == 400
    )
    http.get("/api/sso/microsoft/callback", params={"code": "x", "state": state})  # used up
    again = http.get("/api/sso/microsoft/callback", params={"code": "x", "state": state})
    assert again.status_code == 400 and "already used" in again.text
    assert http.get("/api/sso/google/login").status_code == 400  # not set up
    # a foreign "next" is ignored
    _, out = _sign_in(http, provider, "sub-next", next="https://evil.test")
    assert out["next"] == "teacher"


def test_invite_remove_and_owners(http, provider) -> None:
    _, owner = _sign_in(http, provider, "sub-owner", name="Vlasnica")
    _, other = _sign_in(http, provider, "sub-other", name="Kolega")
    kb = http.post(
        "/api/courses", json={"subject_name": "Tim"}, headers=_auth(owner["token"])
    ).json()
    base = f"/api/courses/{kb['id']}"
    assert http.get(f"{base}/teachers", headers=_auth(other["token"])).status_code == 404
    inv = http.post(f"{base}/invites", headers=_auth(owner["token"])).json()
    code = inv["url"].rsplit("/", 1)[1]
    assert "#/invite/" in inv["url"]
    # an invite link sends a signed-out teacher through sign-in and back to it
    _, via = _sign_in(http, provider, "sub-other", name="Kolega", next=f"invite/{code}")
    assert via["next"] == f"invite/{code}"
    assert (
        http.get(f"/api/invites/{code}", headers=_auth(other["token"])).json()["subject_name"]
        == "Tim"
    )
    assert (
        http.post(f"/api/invites/{code}/accept", headers=_auth(other["token"])).status_code == 200
    )
    assert (
        http.post(f"/api/invites/{code}/accept", headers=_auth(other["token"])).status_code == 404
    )
    team = http.get(f"{base}/teachers", headers=_auth(other["token"])).json()
    assert [(m["name"], m["role"], m["added_via"]) for m in team] == [
        ("Vlasnica", "owner", "owner"),
        ("Kolega", "teacher", "invite"),
    ]
    owner_id, other_id = team[0]["teacher_id"], team[1]["teacher_id"]
    # a teacher cannot invite or remove others; the last owner cannot leave
    assert http.post(f"{base}/invites", headers=_auth(other["token"])).status_code == 403
    r = http.delete(f"{base}/teachers/{owner_id}", headers=_auth(other["token"]))
    assert r.status_code == 409
    r = http.delete(f"{base}/teachers/{owner_id}", headers=_auth(owner["token"]))
    assert r.status_code == 409 and "another owner" in r.text
    # make the colleague an owner, then the first owner may leave
    r = http.post(f"{base}/teachers/{other_id}/owner", headers=_auth(owner["token"]))
    assert [m["role"] for m in r.json()] == ["owner", "owner"]
    assert (
        http.delete(f"{base}/teachers/{owner_id}", headers=_auth(owner["token"])).status_code == 204
    )
    assert http.get(f"{base}/teachers", headers=_auth(owner["token"])).status_code == 404
    # the remaining owner removes nobody else and keeps the course
    assert [
        m["name"] for m in http.get(f"{base}/teachers", headers=_auth(other["token"])).json()
    ] == ["Kolega"]
