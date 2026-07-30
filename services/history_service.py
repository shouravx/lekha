"""services/history_service.py — persisted record of finished (or
failed) translation jobs, backing the Dashboard's "recent translations"
list and the full History page.

Stored as a flat JSON array in data/history.json. This file is small
even after thousands of jobs (each entry is a few hundred bytes), so
unlike checkpoint pages.jsonl it's simply read/written whole — no
streaming needed here.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

import config
from models.job import HistoryEntry, TranslationJob
from services.logger_service import get_logger

logger = get_logger("history_service")
_lock = threading.Lock()


class HistoryService:
    def __init__(self, path: Path = config.HISTORY_FILE) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self._write([])

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    def _read_raw(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read history file, starting fresh: %s", exc)
            return []

    def get_all(self) -> list[HistoryEntry]:
        """Returns all history entries, most recent first."""
        with _lock:
            raw = self._read_raw()
        entries = []
        for d in raw:
            try:
                entries.append(HistoryEntry.from_dict(d))
            except (TypeError, KeyError) as exc:
                logger.warning("Skipping malformed history entry: %s", exc)
        entries.sort(key=lambda e: e.completed_at or e.created_at, reverse=True)
        return entries

    def get_by_id(self, job_id: str) -> Optional[HistoryEntry]:
        for e in self.get_all():
            if e.job_id == job_id:
                return e
        return None

    def add_from_job(self, job: TranslationJob, duration_seconds: float) -> HistoryEntry:
        entry = HistoryEntry(
            job_id=job.job_id,
            filename=job.original_filename,
            source_lang=job.source_lang,
            target_lang=job.target_lang,
            output_formats=list(job.output_formats),
            output_paths=dict(job.output_paths),
            total_pages=job.total_pages,
            status=job.status.value if hasattr(job.status, "value") else str(job.status),
            created_at=job.created_at,
            completed_at=job.completed_at,
            duration_seconds=duration_seconds,
            file_size_mb=job.file_size_mb,
            input_path=job.input_path,
            ocr_enabled=job.ocr_enabled,
        )
        with _lock:
            raw = self._read_raw()
            raw = [r for r in raw if r.get("job_id") != job.job_id]  # replace if re-translated
            raw.append(entry.to_dict())
            self._write(raw)
        logger.info("Recorded history entry for job %s (%s)", job.job_id, entry.status)
        return entry

    def delete(self, job_id: str) -> None:
        with _lock:
            raw = self._read_raw()
            raw = [r for r in raw if r.get("job_id") != job_id]
            self._write(raw)
        logger.info("Deleted history entry for job %s", job_id)

    def search(self, query: str) -> list[HistoryEntry]:
        query = query.strip().lower()
        if not query:
            return self.get_all()
        return [e for e in self.get_all() if query in e.filename.lower()]

    def stats(self) -> dict[str, Any]:
        entries = self.get_all()
        completed = [e for e in entries if e.status == "COMPLETED"]
        return {
            "total_jobs": len(entries),
            "completed_jobs": len(completed),
            "failed_jobs": len([e for e in entries if e.status == "FAILED"]),
            "total_pages_translated": sum(e.total_pages for e in completed),
            "total_time_seconds": sum(e.duration_seconds for e in completed),
        }

    def clear_all(self) -> None:
        with _lock:
            self._write([])
        logger.info("History cleared")


history_service = HistoryService()
