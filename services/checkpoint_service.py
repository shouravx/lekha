"""services/checkpoint_service.py — the resume system.

Design
------
Each job gets its own folder under checkpoints/<job_id>/ containing:

  checkpoint.json   small metadata file: job settings + last_completed_page.
                     Rewritten after every page (cheap, O(1) size).
  pages.jsonl        append-only file, one JSON object per line:
                     {"page": 3, "text": "..."}
                     This is the ground truth of translated content and is
                     read back **line-by-line** (streaming) when building
                     final output documents, so memory use stays flat
                     regardless of document length.

On resume, we trust `checkpoint.json.last_completed_page` to know where to
continue from (O(1)), and we trust pages.jsonl as the source of translated
text for every page from 0..last_completed_page when assembling output.

If the app crashes mid-page, that page's line was never appended to
pages.jsonl, so worst case we simply re-translate one page — no data
corruption, no manual intervention needed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterator, Optional

import config
from services.logger_service import get_logger

logger = get_logger("checkpoint_service")


class CheckpointManager:
    def __init__(self, checkpoints_root: Path = config.CHECKPOINTS_DIR) -> None:
        self.root = Path(checkpoints_root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------
    def _job_dir(self, job_id: str) -> Path:
        d = self.root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _checkpoint_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "checkpoint.json"

    def _pages_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "pages.jsonl"

    # -- lifecycle -------------------------------------------------------
    def init_checkpoint(self, job_id: str, meta: dict[str, Any]) -> None:
        """Creates (or resets, if none of the job's settings changed) the
        checkpoint metadata file. Called once at job start, before resume
        detection — see `get_resume_page`.
        """
        path = self._checkpoint_path(job_id)
        if path.exists():
            return  # don't clobber an existing, resumable checkpoint
        data = {**meta, "last_completed_page": -1}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        # ensure pages.jsonl exists
        self._pages_path(job_id).touch(exist_ok=True)

    def load_checkpoint(self, job_id: str) -> Optional[dict[str, Any]]:
        path = self._checkpoint_path(job_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupted checkpoint for job %s: %s", job_id, exc)
            return None

    def get_resume_page(self, job_id: str) -> int:
        """Returns the 0-indexed page number to resume from (0 if no
        checkpoint or fresh start)."""
        data = self.load_checkpoint(job_id)
        if not data:
            return 0
        return max(0, int(data.get("last_completed_page", -1)) + 1)

    def update_progress(self, job_id: str, last_completed_page: int, **extra: Any) -> None:
        path = self._checkpoint_path(job_id)
        data = self.load_checkpoint(job_id) or {}
        data["last_completed_page"] = last_completed_page
        data.update(extra)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def append_page(self, job_id: str, page_num: int, text: str) -> None:
        """Appends one page's translated text as a single JSON line.
        Append-mode + flush keeps this safe against abrupt process kills:
        previously written lines are never touched.
        """
        path = self._pages_path(job_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"page": page_num, "text": text}, ensure_ascii=False))
            f.write("\n")
            f.flush()

    def read_pages(self, job_id: str) -> Iterator[tuple[int, str]]:
        """Streams (page_num, text) tuples from pages.jsonl in file order.
        Used only at final-assembly time so the whole document's text is
        never resident in memory simultaneously.
        """
        path = self._pages_path(job_id)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    yield int(obj["page"]), obj.get("text", "")
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Skipping malformed checkpoint line in job %s", job_id)

    def has_resumable_job(self, job_id: str) -> bool:
        data = self.load_checkpoint(job_id)
        return bool(data) and int(data.get("last_completed_page", -1)) >= 0

    def list_resumable_jobs(self) -> list[str]:
        if not self.root.exists():
            return []
        return [p.name for p in self.root.iterdir() if p.is_dir() and (p / "checkpoint.json").exists()]

    def list_incomplete_jobs(self) -> list[dict[str, Any]]:
        """Returns checkpoint metadata for every job whose last known
        status never reached a terminal state — i.e. jobs that were
        interrupted by an app/process crash. Used at startup to offer
        automatic recovery.
        """
        incomplete = []
        for job_id in self.list_resumable_jobs():
            data = self.load_checkpoint(job_id)
            if not data:
                continue
            if data.get("status") not in ("COMPLETED",):
                incomplete.append(data)
        return incomplete

    def reconstruct_job(self, job_id: str):
        """Rebuilds a TranslationJob purely from on-disk checkpoint
        metadata, so an interrupted job can be resumed even after the
        original in-memory job/queue state is gone (e.g. after a crash
        or restart). Returns None if no usable checkpoint exists.
        """
        from models.job import TranslationJob
        from models.enums import JobStatus

        data = self.load_checkpoint(job_id)
        if not data or "input_path" not in data:
            return None

        return TranslationJob(
            job_id=job_id,
            input_path=data["input_path"],
            source_lang=data["source_lang"],
            target_lang=data["target_lang"],
            output_formats=data.get("output_formats", ["docx"]),
            ocr_enabled=data.get("ocr_enabled", False),
            output_dir=data.get("output_dir", ""),
            original_filename=data.get("original_filename", Path(data["input_path"]).name),
            status=JobStatus.QUEUED,
            total_pages=data.get("total_pages", 0),
            current_page=int(data.get("last_completed_page", -1)) + 1,
            created_at=data.get("created_at", ""),
            file_size_mb=data.get("file_size_mb", 0.0),
        )

    def delete_checkpoint(self, job_id: str) -> None:
        d = self.root / job_id
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            logger.info("Deleted checkpoint for job %s", job_id)


# A single shared instance is sufficient — all methods are stateless
# beyond simple file I/O, which is itself atomic enough for a
# single-process, single-job-at-a-time design (see MAX_CONCURRENT_JOBS).
checkpoint_manager = CheckpointManager()
