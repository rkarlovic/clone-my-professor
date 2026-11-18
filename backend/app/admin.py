"""Admin API: register learning platforms (LTI) and their Web Services, instead of the command
line. Protected by `ADMIN_TOKEN` (from .env, at least 32 characters) in the X-CMP-Admin-Token
header; without that setting the admin API is closed. CMP has no admin accounts yet; this is
for whoever runs the installation.

    GET  /api/admin/tool                 the URLs to enter in the LMS
    GET  /api/admin/platforms            registered platforms
    POST /api/admin/platforms            register one
    PUT  /api/admin/platforms/{id}       change one (incl. Web Services)
"""

import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine
from core.lti import tool_urls
from core.models import LtiPlatform
from core.settings import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(x_cmp_admin_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().admin_token
    if not expected or len(expected) < 32:
        raise HTTPException(403, "the admin API is not enabled (set ADMIN_TOKEN)")
    if not x_cmp_admin_token or not secrets.compare_digest(x_cmp_admin_token, expected):
        raise HTTPException(401, "wrong admin token")


Admin = Annotated[None, Depends(require_admin)]


class PlatformIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # LTI 1.3 (how the platform opens CMP). A Teams tenant used only through the CMP tab app
    # (no LTI) may leave them empty: the issuer becomes its Entra ID issuer.
    issuer: str = Field("", max_length=500)
    client_id: str = Field("", max_length=200)
    auth_login_url: str = Field("", max_length=500)
    jwks_url: str = Field("", max_length=500)
    deployment_ids: list[str] = []
    ws_base_url: str | None = Field(None, max_length=500)
    # the NAME of the environment variable that holds the token; never the token itself
    ws_token_env: str | None = Field(None, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    kind: Literal["moodle", "teams", "classroom"] = "moodle"
    # non-secret sync settings: Teams {"tenant_id", "client_id"}; Classroom {"subject"} (the
    # Workspace user the service account acts as). Values are short strings, never secrets.
    sync_config: dict[str, Annotated[str, Field(max_length=300)]] = {}


class PlatformOut(PlatformIn):
    id: uuid.UUID
    ws_token_set: bool  # the named variable holds a value on this server


def _out(p: LtiPlatform) -> PlatformOut:
    import os

    return PlatformOut(
        id=p.id,
        name=p.name,
        issuer=p.issuer,
        client_id=p.client_id,
        auth_login_url=p.auth_login_url,
        jwks_url=p.jwks_url,
        deployment_ids=list(p.deployment_ids or []),
        ws_base_url=p.ws_base_url,
        ws_token_env=p.ws_token_env,
        kind=p.kind or "moodle",
        sync_config=dict(p.sync_config or {}),
        ws_token_set=bool(p.ws_token_env and os.environ.get(p.ws_token_env)),
    )


SYNC_KEYS = {"teams": {"tenant_id", "client_id"}, "classroom": {"subject"}, "moodle": set()}


def _apply(p: LtiPlatform, body: PlatformIn) -> None:
    data = body.model_dump()
    allowed = SYNC_KEYS[body.kind]
    data["sync_config"] = cfg = {k: v.strip() for k, v in body.sync_config.items() if k in allowed}
    lti_fields = ("issuer", "client_id", "auth_login_url", "jwks_url")
    if body.kind == "teams" and not any(data[f].strip() for f in lti_fields):
        # the CMP tab app only (no LTI in Teams): identified by the tenant
        if not cfg.get("tenant_id"):
            raise HTTPException(422, "a Teams tenant needs its tenant id")
        data["issuer"] = f"https://login.microsoftonline.com/{cfg['tenant_id']}/v2.0"
        data["client_id"] = cfg.get("client_id", "")
    elif not all(data[f].strip() for f in lti_fields):
        raise HTTPException(422, "LTI needs the issuer, client id, login URL and key set URL")
    for field, value in data.items():
        setattr(p, field, value)


@router.get("/tool")
def tool(_: Admin) -> dict:
    urls = tool_urls()
    s = get_settings()
    if s.teams_app_client_id:  # the Teams tab app (scripts.teams_app builds its package)
        urls["teams_tab_configuration_url"] = f"{s.public_url.rstrip('/')}/#/teams/config"
    return urls


@router.get("/platforms")
def platforms(_: Admin) -> list[PlatformOut]:
    with Session(app_engine()) as s:
        return [_out(p) for p in s.scalars(select(LtiPlatform).order_by(LtiPlatform.name))]


@router.post("/platforms", status_code=201)
def register(body: PlatformIn, _: Admin) -> PlatformOut:
    p = LtiPlatform()
    _apply(p, body)  # fills the issuer of a Teams tenant without LTI
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        clash = s.scalars(
            select(LtiPlatform).where(
                LtiPlatform.issuer == p.issuer, LtiPlatform.client_id == p.client_id
            )
        ).first()
        if clash:
            raise HTTPException(409, "this issuer and client id are already registered")
        s.add(p)
    return _out(p)


@router.put("/platforms/{platform_id}")
def change(platform_id: uuid.UUID, body: PlatformIn, _: Admin) -> PlatformOut:
    with Session(app_engine(), expire_on_commit=False) as s, s.begin():
        p = s.get(LtiPlatform, platform_id)
        if p is None:
            raise HTTPException(404, "platform not found")
        _apply(p, body)
    return _out(p)
