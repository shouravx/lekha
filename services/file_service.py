"""services/file_service.py — filesystem helpers: validation, sizing,
safe filenames, and cross-platform "open folder" support.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import config
from services.logger_service import get_logger

logger = get_logger("file_service")


class InvalidPDFError(Exception):
    """Raised when an uploaded file fails validation."""


def human_readable_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def get_file_size_mb(path: str | Path) -> float:
    return Path(path).stat().st_size / (1024 * 1024)


def sanitize_filename(filename: str) -> str:
    name = Path(filename).stem
    ext = Path(filename).suffix
    name = re.sub(r"[^\w\-. ]", "_", name).strip()
    return f"{name}{ext}"


def generate_job_id() -> str:
    return uuid.uuid4().hex[:12]


def save_uploaded_file(file_bytes: bytes, original_filename: str) -> Path:
    """Persists an uploaded file to the uploads/ directory with a unique,
    collision-free name while keeping the original name human-readable.
    """
    safe_name = sanitize_filename(original_filename)
    unique_prefix = uuid.uuid4().hex[:8]
    dest = config.UPLOADS_DIR / f"{unique_prefix}_{safe_name}"
    dest.write_bytes(file_bytes)
    logger.info("Saved upload -> %s (%s)", dest, human_readable_size(len(file_bytes)))
    return dest


def validate_pdf_file(path: str | Path, max_size_mb: int = config.MAX_FILE_SIZE_MB) -> None:
    """Validates that a file exists, has a .pdf extension, is within the
    configured size ceiling, and can be opened by PyMuPDF without being
    fully decoded (cheap structural check). Raises InvalidPDFError on any
    failure with a user-friendly message.
    """
    path = Path(path)
    if not path.exists():
        raise InvalidPDFError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise InvalidPDFError("Only .pdf files are supported.")

    size_mb = get_file_size_mb(path)
    if size_mb > max_size_mb:
        raise InvalidPDFError(
            f"File is {size_mb:.0f} MB, which exceeds the {max_size_mb} MB limit "
            "configured in Settings."
        )
    if size_mb <= 0:
        raise InvalidPDFError("File is empty.")

    try:
        import fitz  # local import keeps module import cheap at app startup

        doc = fitz.open(str(path))
        if doc.page_count == 0:
            doc.close()
            raise InvalidPDFError("PDF has no pages.")
        doc.close()
    except InvalidPDFError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InvalidPDFError(f"Could not open PDF (it may be corrupted): {exc}") from exc


def open_in_file_explorer(path: str | Path) -> Optional[str]:
    """Opens the given folder/file in the OS file explorer. Returns an
    error string on failure, or None on success.
    """
    path = Path(path)
    if not path.exists():
        return f"Path does not exist: {path}"
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not open file explorer for %s: %s", path, exc)
        return str(exc)


def ensure_output_dir(job_id: str, base_dir: str | Path | None = None) -> Path:
    base = Path(base_dir) if base_dir else config.OUTPUTS_DIR
    out_dir = base / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir
