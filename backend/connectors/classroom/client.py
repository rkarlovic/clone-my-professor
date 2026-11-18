"""Google Classroom material sync: Classroom API (what is published in a course) + Drive API
(the files), CLAUDE.md "Connectors".

Listed: Drive files attached to the course's **published** materials and assignments (drafts and
scheduled posts are what students cannot see yet, so they are left out). Section = the topic.
Google Docs and Slides are exported (Docs -> .docx, Slides -> .pptx) so Docling keeps their
structure; other files are taken if CMP can parse them (PDF, PPTX, DOCX).

Sign-in: a service account with **domain-wide delegation**, acting as one Workspace user
(`subject`: a dedicated account that is a co-teacher or a Classroom admin), with read-only
scopes only. The Workspace admin grants exactly these scopes to the service account's client id.
The key (the service account's JSON) is read from the environment variable the platform names,
either as the JSON itself or as a path to the file; tokens are only sent to Google's APIs.
"""

import json
import os
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx
import jwt

from connectors.base import Material
from core.storage import SUPPORTED_SUFFIXES

CLASSROOM = "https://classroom.googleapis.com/v1"
DRIVE = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.topics.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)
EXPORTS = {  # Google's own formats -> what CMP parses
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}
TIMEOUT = httpx.Timeout(30.0, read=120.0)


class ClassroomError(RuntimeError):
    """Google refused or answered with an error; the message is safe to show."""


def load_key(value: str) -> dict:
    """The service account key from an env var's value: the JSON itself or a path to it."""
    text = value.strip()
    if not text.startswith("{"):
        text = Path(text).read_text(encoding="utf-8")
    key = json.loads(text)
    if key.get("type") != "service_account":
        raise ClassroomError("the key is not a Google service account key")
    return key


class ClassroomClient:
    def __init__(self, key: dict, subject: str, http: httpx.Client | None = None) -> None:
        self._key, self.subject = key, subject
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self._token: tuple[str, float] | None = None

    def __repr__(self) -> str:  # never the key
        return f"ClassroomClient(account={self._key.get('client_email')!r}, as={self.subject!r})"

    def _bearer(self) -> dict[str, str]:
        if not self._token or self._token[1] < time.time() + 60:
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": self._key["client_email"],
                    "sub": self.subject,
                    "scope": " ".join(SCOPES),
                    "aud": TOKEN_URL,
                    "iat": now,
                    "exp": now + 3600,
                },
                self._key["private_key"],
                algorithm="RS256",
                headers={"kid": self._key.get("private_key_id")},
            )
            r = self.http.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            if r.status_code != 200:
                raise ClassroomError(
                    f"Google refused the sign-in (HTTP {r.status_code}; check domain-wide "
                    "delegation and the scopes)"
                )
            body = r.json()
            self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return {"Authorization": f"Bearer {self._token[0]}"}

    def _get(self, url: str, params: dict | None = None) -> dict:
        if not url.startswith((CLASSROOM + "/", DRIVE + "/")):
            raise ClassroomError("a link points outside Google's APIs; not followed")
        r = self.http.get(url, params=params, headers=self._bearer())
        if r.status_code == 404:
            raise ClassroomError("the course or a file was not found (check the course id)")
        if r.status_code in (401, 403):
            raise ClassroomError("Google refused access (check the account and its scopes)")
        r.raise_for_status()
        return r.json()

    def _pages(self, url: str, key: str, params: dict) -> Iterator[dict]:
        token = None
        while True:
            page = self._get(url, params | ({"pageToken": token} if token else {}))
            yield from page.get(key, [])
            token = page.get("nextPageToken")
            if not token:
                return

    def list_materials(self, course_id: str) -> list[Material]:
        base = f"{CLASSROOM}/courses/{quote(course_id)}"
        topics = {
            t["topicId"]: t.get("name", "") for t in self._pages(f"{base}/topics", "topic", {})
        }
        posts = [
            *self._pages(
                f"{base}/courseWorkMaterials",
                "courseWorkMaterial",
                {"courseWorkMaterialStates": "PUBLISHED"},
            ),
            *self._pages(f"{base}/courseWork", "courseWork", {"courseWorkStates": "PUBLISHED"}),
        ]
        found: dict[str, Material] = {}
        for post in posts:
            if post.get("state", "PUBLISHED") != "PUBLISHED":
                continue
            section = topics.get(post.get("topicId", ""), "")
            for att in post.get("materials", []):
                file_id = ((att.get("driveFile") or {}).get("driveFile") or {}).get("id")
                if file_id and file_id not in found:
                    m = self._material(file_id, section)
                    if m:
                        found[file_id] = m
        return list(found.values())

    def _material(self, file_id: str, section: str) -> Material | None:
        f = self._get(
            f"{DRIVE}/files/{quote(file_id)}",
            {"fields": "id,name,mimeType,size,modifiedTime,version", "supportsAllDrives": "true"},
        )
        mime, name = f.get("mimeType", ""), f.get("name", "")
        if mime in EXPORTS:
            export_mime, suffix = EXPORTS[mime]
            if PurePosixPath(name).suffix.lower() != suffix:
                name += suffix
            url = f"{DRIVE}/files/{quote(file_id)}/export?mimeType={quote(export_mime)}"
            size = int(f.get("version") or 0)  # exports have no size; the version changes
        elif PurePosixPath(name).suffix.lower() in SUPPORTED_SUFFIXES:
            url = f"{DRIVE}/files/{quote(file_id)}?alt=media&supportsAllDrives=true"
            size = int(f.get("size") or 0)
        else:
            return None
        return Material(
            external_id=file_id,
            file_name=name,
            size=size,
            modified=_unix(f.get("modifiedTime", "")),
            section=section,
            url=url,
        )

    def download(self, material: Material) -> Iterator[bytes]:
        if not material.url.startswith(DRIVE + "/"):
            raise ClassroomError("a file link points outside Google Drive; not downloaded")
        with self.http.stream("GET", material.url, headers=self._bearer()) as r:
            if r.status_code in (401, 403):
                raise ClassroomError("Google refused the download (check the account's access)")
            r.raise_for_status()
            yield from r.iter_bytes()


def key_from_env(env_name: str) -> dict:
    value = os.environ.get(env_name or "", "")
    if not value:
        raise ClassroomError(f"the service account key is not set ({env_name})")
    return load_key(value)


def _unix(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0
