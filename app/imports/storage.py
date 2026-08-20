"""Upload quarantine storage.

Files are stored outside the webroot under a generated, non-guessable name. The
client-supplied filename is never used as a filesystem path (plan 9.6).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from app.config import get_settings


class UploadTooLarge(ValueError):
    pass


def quarantine_dir() -> Path:
    path = Path(get_settings().quarantine_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_storage_key() -> str:
    return uuid.uuid4().hex


def path_for(storage_key: str) -> Path:
    # storage_key is always a generated hex uuid; never derived from user input.
    return quarantine_dir() / storage_key


def write_streamed(storage_key: str, chunks, max_bytes: int) -> tuple[int, str]:
    """Write chunks to quarantine, enforcing a byte limit. Returns (size, sha256 hex)."""
    path = path_for(storage_key)
    digest = hashlib.sha256()
    total = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLarge(f"upload exceeds {max_bytes} bytes")
                digest.update(chunk)
                fh.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return total, digest.hexdigest()


def open_for_read(storage_key: str):
    return open(path_for(storage_key), "rb")  # noqa: SIM115


def delete(storage_key: str) -> None:
    path_for(storage_key).unlink(missing_ok=True)


def exists(storage_key: str) -> bool:
    return path_for(storage_key).exists()
