"""services/queue_service.py — the job queue and background execution
manager.

A single, process-wide singleton (`job_manager`) owns:
  * a FIFO queue of pending job_ids (enables batch translation — users can
    drop multiple PDFs and they translate one-at-a-time, which keeps CPU
    and RAM usage predictable on low-end hardware),
  * a single dedicated worker thread that pulls jobs off the queue and
    runs the translation pipeline,
  * thread-safe progress/log dictionaries the UI polls to render the
    Progress page.

Because Python modules are cached in `sys.modules`, this module-level
singleton survives Streamlit's script reruns within the same server
process, so a translation keeps running in the background even while the
user navigates between pages.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import deque
from typing import Any, Optional

from models.enums import JobStatus
from models.job import TranslationJob
from services.logger_service import get_logger

logger = get_logger("queue_service")

_LOG_CAP = 500  # max log lines retained per job (bounded memory)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, TranslationJob] = {}
        self._progress: dict[str, dict[str, Any]] = {}
        self._logs: dict[str, deque] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None
        self._active_job_id: Optional[str] = None

    # -- public API ------------------------------------------------------
    def submit_job(self, job: TranslationJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
            self._logs[job.job_id] = deque(maxlen=_LOG_CAP)
            self._cancel_events[job.job_id] = threading.Event()
            self._progress[job.job_id] = {
                "status": JobStatus.QUEUED.value,
                "current_page": 0,
                "total_pages": job.total_pages,
                "percent": 0.0,
                "eta_seconds": None,
                "elapsed_seconds": 0.0,
                "current_file": job.original_filename,
            }
        self._queue.put(job.job_id)
        self._log(job.job_id, f"Queued '{job.original_filename}' for translation.")
        self._ensure_worker_running()

    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            event = self._cancel_events.get(job_id)
        if event:
            event.set()
            self._log(job_id, "Cancellation requested by user.")

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_progress(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._progress.get(job_id, {}))

    def get_logs(self, job_id: str) -> list[str]:
        with self._lock:
            return list(self._logs.get(job_id, []))

    def get_all_job_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    def get_active_job_id(self) -> Optional[str]:
        with self._lock:
            return self._active_job_id

    def get_queued_job_ids(self) -> list[str]:
        with self._lock:
            return [
                jid
                for jid, j in self._jobs.items()
                if j.status == JobStatus.QUEUED and jid != self._active_job_id
            ]

    def has_active_work(self) -> bool:
        with self._lock:
            active = any(j.status.is_active for j in self._jobs.values())
        return active or not self._queue.empty()

    # -- internal: worker thread -----------------------------------------
    def _ensure_worker_running(self) -> None:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_thread = threading.Thread(
                target=self._worker_loop, name="translation-worker", daemon=True
            )
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        # Local import avoids a circular import at module load time
        # (core.pipeline depends on nothing in services.queue_service,
        # but importing eagerly at module scope would slow app startup).
        from core.pipeline import TranslationPipeline

        while True:
            try:
                job_id = self._queue.get(timeout=2)
            except queue.Empty:
                return  # nothing left to do — thread exits, restarted on next submit_job

            job = self.get_job(job_id)
            if job is None:
                continue

            with self._lock:
                self._active_job_id = job_id

            try:
                job.status = JobStatus.RUNNING
                self._set_progress(job_id, status=JobStatus.RUNNING.value)
                pipeline = TranslationPipeline(
                    job=job,
                    progress_callback=self._make_progress_callback(job_id),
                    log_callback=lambda msg, jid=job_id: self._log(jid, msg),
                    cancel_event=self._cancel_events[job_id],
                )
                pipeline.run()
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.FAILED
                job.error_message = str(exc)
                self._set_progress(job_id, status=JobStatus.FAILED.value)
                self._log(job_id, f"FAILED: {exc}")
                logger.error("Job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
            finally:
                with self._lock:
                    self._active_job_id = None
                self._queue.task_done()

    # -- internal: progress/log plumbing ---------------------------------
    def _make_progress_callback(self, job_id: str):
        def _cb(**kwargs: Any) -> None:
            self._set_progress(job_id, **kwargs)

        return _cb

    def _set_progress(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            self._progress.setdefault(job_id, {})
            self._progress[job_id].update(kwargs)

    def _log(self, job_id: str, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            self._logs.setdefault(job_id, deque(maxlen=_LOG_CAP)).append(line)
        logger.info("[job %s] %s", job_id, message)


job_manager = JobManager()
