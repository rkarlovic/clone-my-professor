"""Admin API: closed without ADMIN_TOKEN, refuses a wrong token, registers platforms."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from core.settings import get_settings

pytestmark = pytest.mark.db
TOKEN = "t" * 40


def test_admin_api(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    http = TestClient(app)
    monkeypatch.setattr(get_settings(), "admin_token", None)
    assert http.get("/api/admin/platforms", headers={"X-CMP-Admin-Token": TOKEN}).status_code == 403
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)
    assert (
        http.get("/api/admin/platforms", headers={"X-CMP-Admin-Token": "x" * 40}).status_code == 401
    )
    me = {"X-CMP-Admin-Token": TOKEN}
    issuer = f"https://admin-{uuid.uuid4().hex[:6]}.test"
    body = {
        "name": "Test",
        "issuer": issuer,
        "client_id": "c1",
        "auth_login_url": f"{issuer}/auth",
        "jwks_url": f"{issuer}/jwks",
        "deployment_ids": ["1"],
    }
    try:
        r = http.post("/api/admin/platforms", json=body, headers=me)
        assert r.status_code == 201 and r.json()["ws_token_set"] is False
        assert http.post("/api/admin/platforms", json=body, headers=me).status_code == 409
        bad = body | {"ws_token_env": "not a name; rm -rf"}
        assert (
            http.put(f"/api/admin/platforms/{r.json()['id']}", json=bad, headers=me).status_code
            == 422
        )
        ok = body | {"ws_base_url": issuer, "ws_token_env": "SOME_TOKEN"}
        assert (
            http.put(f"/api/admin/platforms/{r.json()['id']}", json=ok, headers=me).json()[
                "ws_token_env"
            ]
            == "SOME_TOKEN"
        )
        assert (
            http.get("/api/admin/tool", headers=me).json()["tool_url"].endswith("/api/lti/launch")
        )
    finally:
        with owner_engine.begin() as c:
            c.execute(text("DELETE FROM lti_platform WHERE issuer = :i"), {"i": issuer})


def test_a_teams_tenant_without_lti(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    http = TestClient(app)
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)
    me = {"X-CMP-Admin-Token": TOKEN}
    tenant = str(uuid.uuid4())
    body = {
        "name": "Škola (Teams kartica)",
        "kind": "teams",
        "sync_config": {"tenant_id": tenant, "client_id": "app-1", "ignored": "x"},
        "ws_token_env": "CMP_TEAMS_SECRET",
    }
    try:
        r = http.post("/api/admin/platforms", json=body, headers=me)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["issuer"] == f"https://login.microsoftonline.com/{tenant}/v2.0"
        assert out["sync_config"] == {"tenant_id": tenant, "client_id": "app-1"}
        # without a tenant id, or a Moodle without its LTI fields: refused
        no_tenant = body | {"sync_config": {"client_id": "a"}}
        assert http.post("/api/admin/platforms", json=no_tenant, headers=me).status_code == 422
        moodle = {"name": "M", "kind": "moodle"}
        assert http.post("/api/admin/platforms", json=moodle, headers=me).status_code == 422
    finally:
        with owner_engine.begin() as c:
            c.execute(
                text("DELETE FROM lti_platform WHERE sync_config->>'tenant_id' = :t"), {"t": tenant}
            )
