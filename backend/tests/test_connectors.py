"""Teams (Microsoft Graph) and Google Classroom connectors against simulated APIs: what they
list (only what students see, only files CMP parses), sections, exports, paging, where tokens
go, and which connector a platform gets."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from connectors.classroom.client import ClassroomClient, ClassroomError, load_key
from connectors.teams.client import GRAPH, TeamsClient, TeamsError
from core.models import LtiPlatform
from core.sync import SyncNotConfigured, connector_for, sync_available

# --- Teams ----------------------------------------------------------------------------------


def _graph(requests: list[httpx.Request]):
    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        url = str(req.url)
        if req.url.host == "login.microsoftonline.com":
            form = parse_qs(req.content.decode())
            assert form["grant_type"] == ["client_credentials"] and form["client_secret"] == ["s3"]
            return httpx.Response(200, json={"access_token": "graph-token", "expires_in": 3600})
        if req.url.host == "sharepoint.test":
            assert "authorization" not in req.headers  # the token never leaves Graph
            return httpx.Response(200, content=b"%PDF-1.7 file")
        assert req.headers["authorization"] == "Bearer graph-token"
        if url.endswith("/groups/team-1/drive/root/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "root", "name": "root", "folder": {}},
                        _item("i1", "Predavanje 1.pptx", "General"),
                        _item("i2", "slika.png", "General"),  # not parsed by CMP
                        _item("i3", "Upute.pdf", "Vježbe/Tjedan 2"),
                    ],
                    "@odata.nextLink": f"{GRAPH}/groups/team-1/drive/root/delta?token=p2",
                },
            )
        if "token=p2" in url:
            return httpx.Response(
                200,
                json={
                    "value": [_item("i4", "Staro.docx", "General"), {"id": "i3", "deleted": {}}],
                    "@odata.deltaLink": f"{GRAPH}/groups/team-1/drive/root/delta?token=d",
                },
            )
        if url.endswith("/drives/d1/items/i1/content"):
            return httpx.Response(302, headers={"Location": "https://sharepoint.test/download/i1"})
        return httpx.Response(404)

    return handler


def _item(item_id: str, name: str, folder: str) -> dict:
    return {
        "id": item_id,
        "name": name,
        "size": 1234,
        "file": {"mimeType": "x"},
        "lastModifiedDateTime": "2026-11-10T08:00:00Z",
        "parentReference": {"driveId": "d1", "path": f"/drives/d1/root:/{folder}"},
    }


def test_teams_lists_the_team_library_with_channels_as_sections() -> None:
    requests: list[httpx.Request] = []
    client = TeamsClient(
        "tenant-1", "app-1", "s3", httpx.Client(transport=httpx.MockTransport(_graph(requests)))
    )
    found = {m.file_name: m for m in client.list_materials("team-1")}
    assert set(found) == {"Predavanje 1.pptx", "Staro.docx"}  # png skipped, deleted i3 dropped
    assert found["Predavanje 1.pptx"].section == "General" and found["Staro.docx"].size == 1234
    assert found["Predavanje 1.pptx"].modified == int(
        datetime(2026, 11, 10, 8, tzinfo=UTC).timestamp()
    )
    assert b"".join(client.download(found["Predavanje 1.pptx"])) == b"%PDF-1.7 file"
    logins = [r for r in requests if r.url.host == "login.microsoftonline.com"]
    assert len(logins) == 1 and "/tenant-1/" in str(logins[0].url)  # token reused
    assert "s3" not in repr(client)


def test_teams_refuses_links_outside_graph_and_explains_refusals() -> None:
    def handler(req):
        if req.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403)

    client = TeamsClient("t", "a", "s", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TeamsError, match="permission"):
        client.list_materials("team-1")
    with pytest.raises(TeamsError, match="not followed"):
        client._get("https://evil.test/v1.0/x")


# --- Classroom -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sa_key() -> dict:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return {
        "type": "service_account",
        "client_email": "cmp-sync@project.iam.gserviceaccount.com",
        "private_key_id": "k1",
        "private_key": pem,
        "_public": key.public_key(),
    }


def _google(requests: list[httpx.Request], public_key):
    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        path, q = req.url.path, dict(req.url.params)
        if req.url.host == "oauth2.googleapis.com":
            form = parse_qs(req.content.decode())
            claims = jwt.decode(
                form["assertion"][0],
                public_key,
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            )
            assert claims["sub"] == "sync@school.test" and "readonly" in claims["scope"]
            assert all(s.endswith("readonly") for s in claims["scope"].split())
            return httpx.Response(200, json={"access_token": "g-token", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer g-token"
        if path.endswith("/topics"):
            return httpx.Response(200, json={"topic": [{"topicId": "t1", "name": "Tjedan 1"}]})
        if path.endswith("/courseWorkMaterials"):
            assert q["courseWorkMaterialStates"] == "PUBLISHED"
            if "pageToken" not in q:
                return httpx.Response(
                    200,
                    json={
                        "courseWorkMaterial": [
                            {
                                "topicId": "t1",
                                "state": "PUBLISHED",
                                "materials": [_drive("f1"), {"link": {"url": "https://x"}}],
                            }
                        ],
                        "nextPageToken": "p2",
                    },
                )
            return httpx.Response(
                200,
                json={"courseWorkMaterial": [{"state": "PUBLISHED", "materials": [_drive("f2")]}]},
            )
        if path.endswith("/courseWork"):
            return httpx.Response(
                200,
                json={
                    "courseWork": [
                        {"state": "PUBLISHED", "materials": [_drive("f3"), _drive("f1")]}
                    ]
                },
            )
        files = {
            "f1": {
                "id": "f1",
                "name": "Predavanje 1",
                "mimeType": "application/vnd.google-apps.presentation",
                "version": "17",
                "modifiedTime": "2026-11-10T08:00:00Z",
            },
            "f2": {
                "id": "f2",
                "name": "Skripta.pdf",
                "mimeType": "application/pdf",
                "size": "999",
                "modifiedTime": "2026-11-11T08:00:00Z",
            },
            "f3": {"id": "f3", "name": "Snimka.mp4", "mimeType": "video/mp4", "size": "5"},
        }
        if path.startswith("/drive/v3/files/") and path.endswith("/export"):
            assert q["mimeType"].endswith("presentationml.presentation")
            return httpx.Response(200, content=b"PK pptx")
        if path.startswith("/drive/v3/files/"):
            f = files[path.rsplit("/", 1)[1]]
            if q.get("alt") == "media":
                return httpx.Response(200, content=b"%PDF")
            return httpx.Response(200, json=f)
        return httpx.Response(404)

    return handler


def _drive(file_id: str) -> dict:
    return {"driveFile": {"driveFile": {"id": file_id, "title": file_id}}}


def test_classroom_lists_published_drive_files_and_exports_google_formats(sa_key) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(transport=httpx.MockTransport(_google(requests, sa_key["_public"])))
    client = ClassroomClient(sa_key, "sync@school.test", http)
    found = {m.file_name: m for m in client.list_materials("course-9")}
    assert set(found) == {"Predavanje 1.pptx", "Skripta.pdf"}  # video skipped, f1 once
    slides = found["Predavanje 1.pptx"]
    assert slides.section == "Tjedan 1" and slides.size == 17  # Google's version for exports
    assert found["Skripta.pdf"].size == 999 and found["Skripta.pdf"].section == ""
    assert b"".join(client.download(slides)) == b"PK pptx"
    assert b"".join(client.download(found["Skripta.pdf"])) == b"%PDF"
    assert sum(r.url.host == "oauth2.googleapis.com" for r in requests) == 1
    assert "PRIVATE KEY" not in repr(client)
    with pytest.raises(ClassroomError, match="outside"):
        list(client.download(slides.__class__("x", "x.pdf", 1, 1, url="https://evil.test/f")))


def test_service_account_keys_load_from_json_or_a_path(tmp_path, sa_key) -> None:
    key = {k: v for k, v in sa_key.items() if not k.startswith("_")}
    assert load_key(json.dumps(key))["client_email"] == key["client_email"]
    path = tmp_path / "key.json"
    path.write_text(json.dumps(key), encoding="utf-8")
    assert load_key(str(path))["private_key_id"] == "k1"
    with pytest.raises(ClassroomError, match="service account"):
        load_key(json.dumps({"type": "authorized_user"}))


# --- which connector a platform gets ---------------------------------------------------------


def test_platform_kinds_pick_their_connector(monkeypatch, sa_key) -> None:
    key = json.dumps({k: v for k, v in sa_key.items() if not k.startswith("_")})
    monkeypatch.setenv("CMP_T_SECRET", "s3")
    monkeypatch.setenv("CMP_G_KEY", key)
    teams = LtiPlatform(
        kind="teams", ws_token_env="CMP_T_SECRET", sync_config={"tenant_id": "t", "client_id": "a"}
    )
    classroom = LtiPlatform(
        kind="classroom", ws_token_env="CMP_G_KEY", sync_config={"subject": "sync@x"}
    )
    moodle = LtiPlatform(
        kind="moodle", ws_token_env="CMP_T_SECRET", ws_base_url="http://moodle", issuer="http://m"
    )
    assert isinstance(connector_for(teams), TeamsClient)
    assert isinstance(connector_for(classroom), ClassroomClient)
    assert type(connector_for(moodle)).__name__ == "MoodleClient"
    assert all(sync_available(p) for p in (teams, classroom, moodle))
    half = LtiPlatform(kind="teams", ws_token_env="CMP_T_SECRET", sync_config={"tenant_id": "t"})
    assert not sync_available(half)
    with pytest.raises(SyncNotConfigured):
        connector_for(half)
