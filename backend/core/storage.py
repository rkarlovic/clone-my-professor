"""Uploaded course files on disk, named by content hash: <upload_dir>/<kb_id>/<sha256><suffix>.

The same file uploaded twice (or kept across versions) is stored once. The original file name
lives on the `document` row; it is never used as a path, so a crafted name cannot escape
the upload folder.
"""

import hashlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import BinaryIO

from core.settings import get_settings

SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".docx"}  # what Docling parses for us
CHUNK = 1024 * 1024


class UploadError(ValueError):
    """The file cannot be accepted (type, size, empty); the message is shown to the teacher."""


@dataclass(frozen=True)
class StoredFile:
    file_name: str  # the teacher's name for it (display and citations)
    file_hash: str
    path: Path
    size: int


def clean_file_name(name: str) -> str:
    """The last path part of a browser-supplied name, without control characters."""
    base = PurePath(name.replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    if not base or base in {".", ".."}:
        raise UploadError("the file has no name")
    return base[:300]


def store_upload(kb_id: uuid.UUID, file_name: str, stream: BinaryIO) -> StoredFile:
    """Copy an upload into the store while hashing it; refuses unsupported types and files
    over the size limit before anything is kept."""
    settings = get_settings()
    name = clean_file_name(file_name)
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise UploadError(f"{name}: file type {suffix or '(none)'} is not supported ({allowed})")
    limit = settings.max_upload_mb * 1024 * 1024
    folder = settings.upload_dir / str(kb_id)
    folder.mkdir(parents=True, exist_ok=True)

    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(dir=folder, delete=False, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
        try:
            while block := stream.read(CHUNK):
                size += len(block)
                if size > limit:
                    raise UploadError(f"{name}: larger than {settings.max_upload_mb} MB")
                digest.update(block)
                tmp.write(block)
        except BaseException:
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            raise
    if size == 0:
        tmp_path.unlink(missing_ok=True)
        raise UploadError(f"{name}: the file is empty")

    file_hash = digest.hexdigest()
    target = folder / f"{file_hash}{suffix}"
    if target.exists():
        tmp_path.unlink()
    else:
        shutil.move(tmp_path, target)
    return StoredFile(name, file_hash, target, size)
