"""Material sync from a linked LMS course (fake connector), and the Moodle client's parsing:
only visible, supported files; unchanged files are not downloaded again; changes become a
draft version (rule 5); the token never goes to another host."""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from connectors.base import Material
from connectors.moodle.client import MoodleClient, MoodleError
from core.db import kb_scope
from core.models import (
    Document,
    IngestStatus,
    KbVersion,
    LmsFile,
    LtiPlatform,
    PlatformContextMap,
)
from core.settings import get_settings
from core.sync import sync_course, unique_names


class FakeLms:
    """A course whose files are {external_id: (name, bytes, modified)}."""

    def __init__(self, files: dict[str, tuple[str, bytes, int]]) -> None:
        self.files = files
        self.downloads: list[str] = []

    def list_materials(self, course_id: str) -> list[Material]:
        return [
            Material(eid, name, len(data), modified)
            for eid, (name, data, modified) in self.files.items()
        ]

    def download(self, m: Material):
        self.downloads.append(m.file_name)
        yield self.files[m.external_id][1]


@pytest.fixture
def link(owner_engine: Engine, course, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    kb_id, _ = course
    with Session(owner_engine) as s, s.begin():
        p = LtiPlatform(
            name="Fake LMS",
            issuer=f"https://fake-{uuid.uuid4().hex[:6]}.test",
            client_id="c",
            auth_login_url="x",
            jwks_url="x",
            ws_base_url="https://fake.test",
            ws_token_env="X",
        )
        s.add(p)
        s.flush()
        m = PlatformContextMap(platform_id=p.id, context_id="42", kb_id=kb_id, sync_enabled=True)
        s.add(m)
        s.flush()
        ids = (m.id, p.id)
    yield ids[0], kb_id
    with owner_engine.begin() as c:
        c.execute(text("DELETE FROM lms_file WHERE map_id = :m"), {"m": ids[0]})
        c.execute(text("DELETE FROM platform_context_map WHERE id = :m"), {"m": ids[0]})
        c.execute(text("DELETE FROM lti_platform WHERE id = :p"), {"p": ids[1]})


def _process(app_engine: Engine, kb_id, number: int) -> None:
    """Pretend the ingest job ran: the version is ready and holds its pending files."""
    with Session(app_engine) as s, s.begin():
        v = s.query(KbVersion).filter_by(kb_id=kb_id, version=number).one()
        v.ingest_status = IngestStatus.ready
        vid, pending, base = v.id, v.pending_files, v.base_version_id
    carried = {}
    if base:
        with kb_scope(base, app_engine) as s:
            carried = dict(s.query(Document.file_name, Document.file_hash).all())
    with kb_scope(vid, app_engine) as s:
        for name, h in (carried | {f["file_name"]: f["file_hash"] for f in pending}).items():
            s.add(Document(kb_version_id=vid, file_name=name, file_hash=h, source_platform="lms"))


def test_sync_turns_new_and_changed_files_into_drafts(app_engine: Engine, link) -> None:
    map_id, kb_id = link
    lms = FakeLms(
        {
            "1/a.pdf": ("a.pdf", b"%PDF a v1", 100),
            "2/b.pptx": ("b.pptx", b"PK b v1", 100),
        }
    )
    queued: list = []
    run = lambda: sync_course(  # noqa: E731
        map_id, connector_factory=lambda p: lms, enqueue=lambda v, k: queued.append(v)
    )

    r1 = run()
    assert r1["status"] == "new_version" and r1["version"] == 1
    assert sorted(r1["new_files"]) == ["a.pdf", "b.pptx"] and len(queued) == 1
    with Session(app_engine) as s:
        assert s.query(KbVersion).filter_by(kb_id=kb_id, version=1).one().origin == "lms"

    assert run()["status"] == "busy"  # v1 is still queued: try again next time
    _process(app_engine, kb_id, 1)
    lms.downloads.clear()
    r2 = run()
    assert r2["status"] == "unchanged" and lms.downloads == []  # nothing downloaded again

    lms.files["1/a.pdf"] = ("a.pdf", b"%PDF a v2 (fixed typo)", 200)
    r3 = run()
    assert r3["status"] == "new_version" and r3["new_files"] == ["a.pdf"]
    assert lms.downloads == ["a.pdf"]
    with Session(app_engine) as s:
        link_row = s.get(PlatformContextMap, map_id)
        assert link_row.last_sync_status == "new_version" and link_row.last_sync_at is not None


def test_sync_without_web_services_says_so(app_engine: Engine, link) -> None:
    map_id, _ = link
    report = sync_course(map_id)  # the real factory: the env var "X" holds no token
    assert report["status"] == "not_configured"


def test_duplicate_names_get_a_number() -> None:
    names = unique_names([Material("1/a.pdf", "a.pdf", 1, 1), Material("2/a.pdf", "a.pdf", 1, 1)])
    assert sorted(names.values()) == ["a (2).pdf", "a.pdf"]


def test_moodle_client_takes_only_visible_supported_files(monkeypatch) -> None:
    client = MoodleClient("https://moodle.test", "secret-token")
    contents = [
        {
            "name": "Tjedan 1",
            "visible": 1,
            "modules": [
                {
                    "id": 7,
                    "visible": 1,
                    "uservisible": True,
                    "contents": [
                        {
                            "type": "file",
                            "filename": "Uvod.pptx",
                            "filepath": "/",
                            "filesize": 10,
                            "timemodified": 5,
                            "fileurl": "https://moodle.test/webservice/pluginfile.php/1/Uvod.pptx",
                        },
                        {
                            "type": "file",
                            "filename": "slika.png",
                            "filepath": "/",
                            "filesize": 3,
                            "timemodified": 5,
                            "fileurl": "https://moodle.test/x.png",
                        },
                        {"type": "url", "filename": "link", "fileurl": "https://elsewhere.test"},
                    ],
                },
                {
                    "id": 8,
                    "visible": 0,
                    "contents": [
                        {
                            "type": "file",
                            "filename": "Rjesenja.pdf",
                            "filepath": "/",
                            "filesize": 1,
                            "timemodified": 1,
                            "fileurl": "https://moodle.test/r.pdf",
                        },
                    ],
                },
            ],
        },
        {
            "name": "Skriveno",
            "visible": 0,
            "modules": [
                {
                    "id": 9,
                    "visible": 1,
                    "contents": [
                        {
                            "type": "file",
                            "filename": "Nacrt.pdf",
                            "filepath": "/",
                            "filesize": 1,
                            "timemodified": 1,
                            "fileurl": "https://moodle.test/n.pdf",
                        },
                    ],
                },
            ],
        },
    ]
    monkeypatch.setattr(client, "_call", lambda fn, **kw: contents)
    (m,) = client.list_materials("42")
    assert (m.file_name, m.external_id, m.section, m.size) == (
        "Uvod.pptx",
        "7/Uvod.pptx",
        "Tjedan 1",
        10,
    )
    assert "secret-token" not in repr(m)

    foreign = Material("x", "x.pdf", 1, 1, url="https://evil.test/x.pdf")
    with pytest.raises(MoodleError, match="outside the platform"):
        next(client.download(foreign))


def test_auto_add_sections_publish_only_when_every_new_file_is_in_them(
    app_engine: Engine, link
) -> None:
    map_id, kb_id = link
    with Session(app_engine) as s, s.begin():
        s.get(PlatformContextMap, map_id).auto_add_sections = ["Predavanja"]

    class Sectioned(FakeLms):
        def list_materials(self, course_id):
            return [
                Material(
                    eid, name, len(data), mod, section=("Vježbe" if "vj" in name else "Predavanja")
                )
                for eid, (name, data, mod) in self.files.items()
            ]

    lms = Sectioned({"1/p1.pdf": ("p1.pdf", b"%PDF p1", 1)})
    queued: list = []

    def run():
        return sync_course(
            map_id, connector_factory=lambda p: lms, enqueue=lambda v, k: queued.append(v)
        )

    r = run()
    assert r["status"] == "new_version" and r["published"] is True
    with Session(app_engine) as s:
        assert s.query(KbVersion).filter_by(kb_id=kb_id, version=1).one().auto_activate
        assert {f.section for f in s.query(LmsFile).filter_by(map_id=map_id)} == {"Predavanja"}
    _process(app_engine, kb_id, 1)
    lms.files["2/vj1.pdf"] = ("vj1.pdf", b"%PDF vj1", 1)  # outside the auto-add sections
    lms.files["3/p2.pdf"] = ("p2.pdf", b"%PDF p2", 1)
    r = run()
    assert r["status"] == "new_version" and r["published"] is False  # all wait for review


def test_moodle_file_links_are_fetched_through_the_internal_address(monkeypatch) -> None:
    import httpx

    seen = []

    class Resp:
        headers = {"content-type": "application/pdf"}

        def raise_for_status(self):
            return self

        def iter_bytes(self):
            yield b"%PDF"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream", lambda method, url, timeout: seen.append(url) or Resp())
    client = MoodleClient("http://moodle", "tok", public_url="http://127.0.0.1:8080")
    m = Material("1", "a.pdf", 4, 1, url="http://127.0.0.1:8080/webservice/pluginfile.php/5/a.pdf")
    assert b"".join(client.download(m)) == b"%PDF"
    assert seen[0].startswith("http://moodle/webservice/pluginfile.php/5/a.pdf?token=tok")
