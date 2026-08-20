"""Upload validation: extension, signature, container structure.

CSV and XLSX are the only accepted formats (plan 9.6). Macro-enabled workbooks and
external links are rejected. XLSX validation inspects the zip container without ever
decompressing beyond a bounded size, defending against zip-bomb style expansion before
handing the file to openpyxl.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.config import get_settings

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
_XLSX_SIGNATURE = b"PK\x03\x04"
_REQUIRED_XLSX_PARTS = {"[Content_Types].xml", "xl/workbook.xml"}
_FORBIDDEN_XLSX_PREFIXES = ("xl/vbaProject", "xl/externalLinks/")


class UploadValidationError(ValueError):
    pass


def check_extension(display_filename: str) -> str:
    ext = Path(display_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(f"unsupported file extension: {ext or '(none)'}")
    return ext


def _peek(path: Path, nbytes: int = 8) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(nbytes)


def validate_csv_signature(path: Path) -> None:
    head = _peek(path, 4)
    if head.startswith(_XLSX_SIGNATURE):
        raise UploadValidationError("file has a .csv extension but a ZIP/XLSX signature")


def validate_xlsx_container(path: Path) -> None:
    head = _peek(path, 4)
    if not head.startswith(_XLSX_SIGNATURE):
        raise UploadValidationError("file does not have a valid XLSX (ZIP) signature")

    settings = get_settings()
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if not _REQUIRED_XLSX_PARTS.issubset(names):
                raise UploadValidationError("XLSX container is missing required parts")
            for forbidden in _FORBIDDEN_XLSX_PREFIXES:
                if any(name.startswith(forbidden) for name in names):
                    raise UploadValidationError(
                        "macro-enabled or externally linked workbooks are not accepted"
                    )
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > settings.upload_max_expanded_bytes:
                raise UploadValidationError("expanded XLSX content exceeds the allowed size")
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("XLSX container is not a valid ZIP archive") from exc


def validate_upload(path: Path, display_filename: str) -> str:
    """Validate extension, signature, and container. Returns the validated extension."""
    ext = check_extension(display_filename)
    if ext == ".csv":
        validate_csv_signature(path)
    else:
        validate_xlsx_container(path)
    return ext
