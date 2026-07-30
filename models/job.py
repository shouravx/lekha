"""models/job.py — data structures describing a translation job and a
completed history record. Kept dependency-free (no Streamlit imports) so
they can be reused by core/services without coupling to the UI layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from models.enums import JobStatus


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class TranslationJob:
    """Represents a single translation request, from upload to output."""

    input_path: str
    source_lang: str
    target_lang: str
    output_formats: list[str]
    ocr_enabled: bool = False
    output_dir: str = ""
    job_id: str = field(default_factory=new_job_id)
    original_filename: str = ""
    status: JobStatus = JobStatus.QUEUED
    total_pages: int = 0
    current_page: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    output_paths: dict[str, str] = field(default_factory=dict)
    file_size_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, JobStatus) else self.status
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TranslationJob":
        d = dict(d)
        status = d.get("status", JobStatus.QUEUED)
        d["status"] = JobStatus(status) if not isinstance(status, JobStatus) else status
        return TranslationJob(**d)


@dataclass
class HistoryEntry:
    """A persisted record of a finished (or failed) translation job."""

    job_id: str
    filename: str
    source_lang: str
    target_lang: str
    output_formats: list[str]
    output_paths: dict[str, str]
    total_pages: int
    status: str
    created_at: str
    completed_at: Optional[str]
    duration_seconds: float
    file_size_mb: float
    input_path: str = ""
    ocr_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "HistoryEntry":
        return HistoryEntry(**d)
