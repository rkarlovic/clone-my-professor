"""LTI 1.3 launch against a simulated platform (its own RSA key): login, launch validation,
teacher course linking, student chat access limited to the linked course, refusals, rule 9."""

import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from core import lti
from core.lti import CLAIM, TEACHER_ROLES, tool_urls, user_ref
from core.models import LtiPlatform, Teacher

pytestmark = pytest.mark.db
ISSUER = "https://lms.test"
LEARNER = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"


def _key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "k1"}
    return key, {"keys": [jwk]}


@pytest.fixture
def platform(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch):
    key, jwks = _key()
    client_id = f"client-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(lti, "_platform_keys", lambda url, refresh=False: jwks)
    with Session(owner_engine) as s, s.begin():
        p = LtiPlatform(
            name="Test LMS",
            issuer=ISSUER,
            client_id=client_id,
            auth_login_url=f"{ISSUER}/auth",
            jwks_url=f"{ISSUER}/jwks",
            deployment_ids=["dep-1"],
        )
        s.add(p)
        s.flush()
        pid = p.id
    yield {"id": pid, "client_id": client_id, "key": key}
    with owner_engine.begin() as c:
        kbs = [
            r[0]
            for r in c.execute(
                text("SELECT kb_id FROM platform_context_map WHERE platform_id = :p"), {"p": pid}
            )
        ]
        # only what this test created: its own LMS teacher (by the hash of its test user id)
        ref = {"r": user_ref(ISSUER, "teacher-1")}
        mine = "SELECT id FROM teacher WHERE external_ref = :r"
        c.execute(text("DELETE FROM platform_context_map WHERE platform_id = :p"), {"p": pid})
        c.execute(text("DELETE FROM lti_login_state WHERE platform_id = :p"), {"p": pid})
        theirs = f"SELECT id FROM knowledge_base WHERE teacher_id IN ({mine})"
        c.execute(text(f"DELETE FROM platform_context_map WHERE kb_id IN ({theirs})"), ref)
        for kb in kbs:
            c.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb})
        c.execute(text(f"DELETE FROM knowledge_base WHERE teacher_id IN ({mine})"), ref)
        c.execute(text(f"DELETE FROM teacher WHERE id IN ({mine})"), ref)
        c.execute(text("DELETE FROM lti_platform WHERE id = :p"), {"p": pid})


@pytest.fixture
def http():
    from app.main import app

    return TestClient(app, follow_redirects=False)


def _login(http, client_id: str) -> dict:
    r = http.get(
        "/api/lti/login",
        params={
            "iss": ISSUER,
            "login_hint": "u1",
            "target_link_uri": tool_urls()["tool_url"],
            "client_id": client_id,
            "lti_message_hint": "m1",
        },
    )
    assert r.status_code == 302, r.text
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/auth"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["redirect_uri"] == tool_urls()["tool_url"] and q["response_mode"] == "form_post"
    return q


def _token(
    platform, login_nonce, *, sub="lms-user-7", roles=(LEARNER,), context="course-42", **over
):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": platform["client_id"],
        "sub": sub,
        "iat": now,
        "exp": now + 300,
        "nonce": login_nonce,
        "name": "Ana Anić",  # personal data the tool must not keep
        "email": "ana@lms.test",
        CLAIM + "message_type": "LtiResourceLinkRequest",
        CLAIM + "version": "1.3.0",
        CLAIM + "deployment_id": "dep-1",
        CLAIM + "roles": list(roles),
        CLAIM + "context": {"id": context, "title": "Rad s darovitom djecom 2026/27"},
        CLAIM + "resource_link": {"id": "rl-1"},
    } | over
    return jwt.encode(claims, platform.get("sign_key", platform["key"]), "RS256", {"kid": "k1"})


def _launch(http, platform, **kw):
    q = _login(http, platform["client_id"])
    r = http.post(
        "/api/lti/launch",
        data={"id_token": _token(platform, q["nonce"], **kw), "state": q["state"]},
    )
    return r, q


def _fragment(r) -> dict:
    assert r.status_code == 303, r.text
    frag = urlparse(r.headers["location"]).fragment  # "/lti?next=...&token=..."
    return {k: v[0] for k, v in parse_qs(frag.split("?", 1)[1]).items()}


def test_teacher_links_the_course_then_students_get_its_chat(http, platform, app_engine) -> None:
    teacher_roles = (TEACHER_ROLES[0],)
    r, _ = _launch(http, platform, sub="teacher-1", roles=teacher_roles)
    first = _fragment(r)
    assert first["next"] == "link"
    auth = {"Authorization": f"Bearer {first['token']}"}

    info = http.get("/api/lti/link", headers=auth).json()
    assert info == {"context_title": "Rad s darovitom djecom 2026/27", "courses": []}
    assert http.post("/api/lti/link", json={}, headers=auth).status_code == 422
    linked = http.post("/api/lti/link", json={"new_course_name": "Darovitost"}, headers=auth)
    assert linked.status_code == 200
    kb_id, teacher_token = linked.json()["kb_id"], linked.json()["token"]
    courses = http.get("/api/courses", headers={"Authorization": f"Bearer {teacher_token}"})
    assert [c["subject_name"] for c in courses.json()] == ["Darovitost"]

    # the teacher's next launch goes straight to the course page
    r, _ = _launch(http, platform, sub="teacher-1", roles=teacher_roles)
    assert _fragment(r)["next"] == f"teacher/{kb_id}"

    # a student: the chat of the linked course only
    r, _ = _launch(http, platform)
    student = _fragment(r)
    assert student["next"] == f"chat/{kb_id}"
    sauth = {"Authorization": f"Bearer {student['token']}"}
    assert http.get(f"/api/chat/{kb_id}", headers=sauth).status_code == 200
    assert http.get(f"/api/chat/{uuid.uuid4()}", headers=sauth).status_code == 403
    assert http.get("/api/courses", headers=sauth).status_code == 403  # not a teacher

    # rule 9: only an opaque hash of (issuer, user id); no name, no e-mail, no LMS id
    with Session(app_engine) as s:
        t = s.query(Teacher).filter(Teacher.external_ref == user_ref(ISSUER, "teacher-1")).one()
        assert t.name == "Nastavnik (LMS)" and "teacher-1" not in t.external_ref


def test_a_student_of_an_unlinked_course_gets_no_session(http, platform) -> None:
    r, _ = _launch(http, platform, context="course-not-linked")
    assert _fragment(r) == {"next": "not-linked"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"aud": "someone-else"}, "not valid"),
        ({"exp": int(time.time()) - 3600}, "not valid"),
        ({"nonce": "other"}, "does not belong"),
        ({CLAIM + "deployment_id": "dep-9"}, "deployment"),
        ({CLAIM + "message_type": "LtiDeepLinkingRequest"}, "only course links"),
        ({CLAIM + "version": "1.1"}, "only LTI 1.3"),
        ({CLAIM + "context": {}}, "does not name a course"),
    ],
)
def test_bad_launches_are_refused(http, platform, change, message) -> None:
    r, _ = _launch(http, platform, **change)
    assert r.status_code == 400 and message in r.text


def test_foreign_signatures_and_replays_are_refused(http, platform) -> None:
    other, _ = _key()
    r, _ = _launch(http, platform | {"sign_key": other})
    assert r.status_code == 400 and "not valid" in r.text

    q = _login(http, platform["client_id"])
    form = {"id_token": _token(platform, q["nonce"]), "state": q["state"]}
    assert http.post("/api/lti/launch", data=form).status_code == 303
    replay = http.post("/api/lti/launch", data=form)
    assert replay.status_code == 400 and "already used" in replay.text


def test_login_refuses_unknown_platforms_and_foreign_targets(http, platform) -> None:
    base = {"iss": ISSUER, "login_hint": "u", "client_id": platform["client_id"]}
    r = http.get("/api/lti/login", params=base | {"target_link_uri": "https://evil.test/"})
    assert r.status_code == 400 and "target_link_uri" in r.text
    r = http.get(
        "/api/lti/login",
        params=base | {"iss": "https://other.test", "target_link_uri": tool_urls()["tool_url"]},
    )
    assert r.status_code == 400 and "not registered" in r.text
    assert http.get("/api/lti/jwks").json()["keys"][0]["alg"] == "RS256"
