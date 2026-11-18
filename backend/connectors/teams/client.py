"""Microsoft Teams material sync through Microsoft Graph (CLAUDE.md "Connectors").

A team's files live in its SharePoint document library: one folder per channel. The connector
lists the library with a **delta query** (`/groups/{team}/drive/root/delta`; without a stored
delta link it enumerates everything, which the core then compares by size and modification
time) and downloads a file through `/drives/{drive}/items/{item}/content`.

Sign-in: the app's own identity (client credentials) in the institution's Entra ID tenant, with
admin consent for an **application** permission that only reads files (`Files.Read.All`, or
`Sites.Selected` granted per team site for least privilege). The client secret is read from the
environment variable the platform names; tokens are only ever sent to Graph and Entra ID.

Section = the channel (top folder). Only files CMP can parse; deleted items are left out, so a
file deleted in Teams disappears from the listing and the core removes it (as for Moodle).
"""

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from connectors.base import Material
from core.storage import SUPPORTED_SUFFIXES

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
TIMEOUT = httpx.Timeout(30.0, read=120.0)


class TeamsError(RuntimeError):
    """Graph refused or answered with an error; the message is safe to show."""


class TeamsClient:
    def __init__(
        self, tenant_id: str, client_id: str, secret: str, http: httpx.Client | None = None
    ) -> None:
        self.tenant, self.client_id, self._secret = tenant_id, client_id, secret
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self._token: tuple[str, float] | None = None

    def __repr__(self) -> str:  # never the secret
        return f"TeamsClient(tenant={self.tenant!r}, client_id={self.client_id!r})"

    def _bearer(self) -> dict[str, str]:
        if not self._token or self._token[1] < time.time() + 60:
            r = self.http.post(
                f"{LOGIN}/{quote(self.tenant)}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            if r.status_code != 200:
                raise TeamsError(f"Entra ID refused the sign-in (HTTP {r.status_code})")
            body = r.json()
            self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return {"Authorization": f"Bearer {self._token[0]}"}

    def _get(self, url: str) -> dict:
        if not url.startswith(GRAPH + "/"):
            raise TeamsError("a Graph link points elsewhere; not followed")
        r = self.http.get(url, headers=self._bearer())
        if r.status_code == 404:
            raise TeamsError("the team or its files were not found (check the team id)")
        if r.status_code in (401, 403):
            raise TeamsError("Graph refused access (check the app's permission and consent)")
        r.raise_for_status()
        return r.json()

    def list_materials(self, course_id: str) -> list[Material]:
        """`course_id`: the team's group id."""
        url = f"{GRAPH}/groups/{quote(course_id)}/drive/root/delta"
        found: dict[str, Material] = {}
        while url:
            page = self._get(url)
            for item in page.get("value", []):
                m = _material(item)
                if m:
                    found[m.external_id] = m
                elif "deleted" in item:  # Graph marks it with an empty object
                    found.pop(item.get("id", ""), None)
            url = page.get("@odata.nextLink", "")
        return list(found.values())

    def download(self, material: Material) -> Iterator[bytes]:
        if not material.url.startswith(GRAPH + "/"):
            raise TeamsError("a file link points outside Graph; not downloaded")
        # Graph answers with a redirect to a pre-authorised SharePoint URL; httpx drops the
        # Authorization header when a redirect leaves the origin, so the token stays with Graph.
        with self.http.stream(
            "GET", material.url, headers=self._bearer(), follow_redirects=True
        ) as r:
            r.raise_for_status()
            yield from r.iter_bytes()


def _material(item: dict) -> Material | None:
    if "deleted" in item or "file" not in item:
        return None
    name = item.get("name", "")
    if PurePosixPath(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        return None
    parent = item.get("parentReference") or {}
    # "/drives/{id}/root:/General/Week 1" -> channel "General"
    path = parent.get("path", "").partition("root:")[2].strip("/")
    drive = parent.get("driveId", "")
    modified = item.get("lastModifiedDateTime", "")
    return Material(
        external_id=item["id"],
        file_name=name,
        size=int(item.get("size") or 0),
        modified=_unix(modified),
        section=path.split("/")[0] if path else "",
        url=f"{GRAPH}/drives/{quote(drive)}/items/{quote(item['id'])}/content",
    )


def _unix(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0
