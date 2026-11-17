"""The connector interface (CLAUDE.md "Connectors"): thin translators between an LMS and the
core. All logic (versions, approval, chunking) stays in the core; a connector only lists a
course's materials and downloads one.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Material:
    """A file in an LMS course, as the LMS describes it."""

    external_id: str  # stable within the course (e.g. module id + path + name); no secrets
    file_name: str
    size: int
    modified: int  # Unix time of the LMS's last change
    section: str = ""
    # Where to fetch it (may be needed by `download`); not part of its identity, never logged.
    url: str = field(default="", compare=False, repr=False)


class Connector(Protocol):
    def list_materials(self, course_id: str) -> list[Material]: ...

    def download(self, material: Material) -> Iterator[bytes]:
        """The file's bytes, in chunks."""
        ...
