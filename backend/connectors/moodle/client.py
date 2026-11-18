"""Moodle Web Services (REST) for material sync: `core_course_get_contents` lists a course's
modules and their files; files are downloaded from `webservice/pluginfile.php` with the token.

Least privilege: the token belongs to a dedicated sync user that may only call
`core_course_get_contents` (and download files). Only what students can see is taken: hidden
modules and sections are skipped, so drafts the teacher keeps hidden never enter CMP.
The token is sent only to the platform's own address (a file URL pointing elsewhere is refused).
"""

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from connectors.base import Material
from core.storage import SUPPORTED_SUFFIXES

TIMEOUT = httpx.Timeout(30.0, read=120.0)


class MoodleError(RuntimeError):
    """Moodle refused or answered with an error; the message is safe to show."""


class MoodleClient:
    def __init__(self, base_url: str, token: str, public_url: str | None = None) -> None:
        """`base_url`: where CMP reaches Moodle. `public_url`: Moodle's own address (its
        wwwroot, = the LTI issuer) when it differs, e.g. inside Docker; file links Moodle
        returns with that address are fetched through `base_url` instead."""
        self.base = base_url.rstrip("/")
        self.token = token
        self._origin = urlsplit(self.base)[:2]
        self._public = urlsplit(public_url.rstrip("/"))[:2] if public_url else None

    def _call(self, function: str, **params) -> object:
        r = httpx.post(
            f"{self.base}/webservice/rest/server.php",
            data={"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json"}
            | params,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and "exception" in body:
            raise MoodleError(f"{function}: {body.get('errorcode')} ({body.get('message')})")
        return body

    def list_materials(self, course_id: str) -> list[Material]:
        sections = self._call("core_course_get_contents", courseid=course_id)
        found: list[Material] = []
        for section in sections:
            if not section.get("visible", 1) or section.get("uservisible") is False:
                continue
            for module in section.get("modules", []):
                if not module.get("visible", 1) or module.get("uservisible") is False:
                    continue
                for c in module.get("contents") or []:
                    if c.get("type") != "file":
                        continue
                    name = c.get("filename", "")
                    if PurePosixPath(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                        continue
                    found.append(
                        Material(
                            external_id=f"{module['id']}{c.get('filepath', '/')}{name}",
                            file_name=name,
                            size=int(c.get("filesize") or 0),
                            modified=int(c.get("timemodified") or 0),
                            section=section.get("name", ""),
                            url=c.get("fileurl", ""),
                        )
                    )
        return found

    def download(self, material: Material) -> Iterator[bytes]:
        parts = urlsplit(material.url)
        if self._public and parts[:2] == self._public:  # Moodle's public address -> ours
            parts = parts._replace(scheme=self._origin[0], netloc=self._origin[1])
        if parts[:2] != self._origin:
            raise MoodleError("a file URL points outside the platform; not downloaded")
        query = dict(parse_qsl(parts.query)) | {"token": self.token}
        with httpx.stream(
            "GET", urlunsplit(parts._replace(query=urlencode(query))), timeout=TIMEOUT
        ) as r:
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("application/json"):
                raise MoodleError("Moodle refused the download (check the sync user's rights)")
            yield from r.iter_bytes()
