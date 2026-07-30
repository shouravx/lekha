"""core/pipeline.py — orchestrates a single TranslationJob end-to-end:

    extract page -> (OCR if scanned) -> chunk -> translate chunks ->
    checkpoint page -> ... -> build output document(s)

Threading model
---------------
A bounded ThreadPoolExecutor translates the chunks *within* a single
page concurrently (translation is the CPU-bound bottleneck; PDF
extraction and disk I/O are comparatively cheap). Pages themselves are
still processed strictly in order, one at a time, which keeps peak
memory flat and keeps checkpoint writes monotonic (required for the
resume system's "last_completed_page" invariant to hold).

Resilience
----------
* A single corrupted page never aborts the job — PDFExtractor already
  guarantees this, and the pipeline simply records the page as
  untranslated text on failure.
* The job can be cancelled cleanly via threading.Event — checked
  between pages so cancellation never leaves a half-written checkpoint
  page.
* On resume, already-checkpointed pages are skipped entirely (no
  re-translation), and output documents are rebuilt from the full
  pages.jsonl, not just the newly-translated remainder.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import config
from core import chunker, document_builder
from core.pdf_extractor import PDFCorruptedError, PDFExtractor
from core.translator_engine import ArgosTranslatorEngine, ModelNotInstalledError
from models.enums import JobStatus
from models.job import TranslationJob
from services.checkpoint_service import checkpoint_manager
from services.file_service import ensure_output_dir, sanitize_filename
from services.history_service import history_service
from services.logger_service import get_logger
from services.settings_service import settings_service

logger = get_logger("pipeline")

ProgressCallback = Callable[..., None]
LogCallback = Callable[[str], None]


class JobCancelledError(Exception):
    """Raised internally to unwind cleanly when the user cancels a job."""


class TranslationPipeline:
    def __init__(
        self,
        job: TranslationJob,
        progress_callback: ProgressCallback,
        log_callback: LogCallback,
        cancel_event: threading.Event,
    ) -> None:
        self.job = job
        self.progress = progress_callback
        self.log = log_callback
        self.cancel_event = cancel_event
        self.engine = ArgosTranslatorEngine.instance()

        settings = settings_service.get_all()
        self.max_chunk_chars = settings.get("chunk_max_chars", config.MAX_CHUNK_CHARS)
        self.workers = max(1, int(settings.get("translation_workers", config.TRANSLATION_WORKERS)))
        self.ocr_dpi = settings.get("ocr_dpi", config.OCR_RENDER_DPI)

        self._page_durations: list[float] = []  # rolling window for ETA

    # -- public entrypoint ---------------------------------------------------
    def run(self) -> None:
        job = self.job
        job.started_at = job.started_at or datetime.now().isoformat(timespec="seconds")
        start_time = time.monotonic()

        try:
            if not self.engine.is_pair_available(job.source_lang, job.target_lang):
                raise ModelNotInstalledError(
                    f"Translation model '{job.source_lang}'->'{job.target_lang}' is not installed. "
                    "Run `python scripts/download_models.py` once with internet access."
                )

            self.log(f"Starting translation of '{job.original_filename}'.")
            self._translate_pages()

            if self.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                self.log("Job cancelled by user.")
                self.progress(status=JobStatus.CANCELLED.value)
                return

            self.log("All pages translated. Building output document(s)...")
            self._build_outputs()

            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now().isoformat(timespec="seconds")
            self.progress(status=JobStatus.COMPLETED.value, percent=100.0, eta_seconds=0)
            self.log(f"Translation complete: {job.original_filename}")

            duration = time.monotonic() - start_time
            history_service.add_from_job(job, duration_seconds=duration)

            if not settings_service.get("keep_checkpoints_after_completion", False):
                checkpoint_manager.delete_checkpoint(job.job_id)

        except JobCancelledError:
            job.status = JobStatus.CANCELLED
            self.progress(status=JobStatus.CANCELLED.value)
            self.log("Job cancelled by user.")

        except (PDFCorruptedError, ModelNotInstalledError) as exc:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.completed_at = datetime.now().isoformat(timespec="seconds")
            self.progress(status=JobStatus.FAILED.value)
            self.log(f"FAILED: {exc}")
            history_service.add_from_job(job, duration_seconds=time.monotonic() - start_time)
            raise

        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.completed_at = datetime.now().isoformat(timespec="seconds")
            self.progress(status=JobStatus.FAILED.value)
            self.log(f"FAILED: {exc}")
            history_service.add_from_job(job, duration_seconds=time.monotonic() - start_time)
            raise

    # -- stage 1: extraction + translation + checkpointing -------------------
    def _translate_pages(self) -> None:
        job = self.job

        with PDFExtractor(job.input_path) as extractor:
            job.total_pages = extractor.page_count

            checkpoint_manager.init_checkpoint(
                job.job_id,
                meta={
                    "job_id": job.job_id,
                    "input_path": job.input_path,
                    "original_filename": job.original_filename,
                    "source_lang": job.source_lang,
                    "target_lang": job.target_lang,
                    "output_formats": job.output_formats,
                    "ocr_enabled": job.ocr_enabled,
                    "output_dir": job.output_dir,
                    "total_pages": job.total_pages,
                    "created_at": job.created_at,
                    "status": JobStatus.RUNNING.value,
                },
            )

            resume_page = checkpoint_manager.get_resume_page(job.job_id)
            if resume_page > 0:
                self.log(f"Resuming from page {resume_page + 1} of {job.total_pages}.")
            job.current_page = resume_page

            self.progress(
                status=JobStatus.RUNNING.value,
                total_pages=job.total_pages,
                current_page=resume_page,
                percent=self._percent(resume_page, job.total_pages),
                current_file=job.original_filename,
            )

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for page in extractor.iter_pages(start_page=resume_page):
                    if self.cancel_event.is_set():
                        return

                    page_start = time.monotonic()
                    translated_text = self._translate_one_page(page, pool)

                    checkpoint_manager.append_page(job.job_id, page.page_num, translated_text)
                    checkpoint_manager.update_progress(
                        job.job_id,
                        last_completed_page=page.page_num,
                        status=JobStatus.RUNNING.value,
                    )

                    job.current_page = page.page_num + 1
                    elapsed_page = time.monotonic() - page_start
                    self._record_page_duration(elapsed_page)

                    percent = self._percent(job.current_page, job.total_pages)
                    eta = self._estimate_eta(job.current_page, job.total_pages)
                    self.progress(
                        current_page=job.current_page,
                        total_pages=job.total_pages,
                        percent=percent,
                        eta_seconds=eta,
                    )

                    if (page.page_num + 1) % 25 == 0 or page.page_num == job.total_pages - 1:
                        self.log(
                            f"Translated page {page.page_num + 1}/{job.total_pages} "
                            f"({percent:.1f}%, ETA {self._format_eta(eta)})"
                        )

                    if page.had_error:
                        self.log(f"Warning: page {page.page_num + 1} had an extraction error and was skipped.")

    def _translate_one_page(self, page, pool: ThreadPoolExecutor) -> str:
        job = self.job
        raw_text = page.text

        if page.needs_ocr and job.ocr_enabled:
            raw_text = self._ocr_page(page.page_num) or raw_text

        if not raw_text or not raw_text.strip():
            return ""

        chunks = chunker.split_into_chunks(raw_text, max_chars=self.max_chunk_chars)
        if not chunks:
            return ""

        if len(chunks) == 1:
            translated_chunks = [self._translate_chunk_safe(chunks[0])]
        else:
            translated_chunks = list(pool.map(self._translate_chunk_safe, chunks))

        return "\n".join(translated_chunks)

    def _translate_chunk_safe(self, chunk: str) -> str:
        try:
            return self.engine.translate(chunk, self.job.source_lang, self.job.target_lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chunk translation failed, keeping original text: %s", exc)
            return chunk

    def _ocr_page(self, page_num: int) -> str:
        try:
            from core.ocr_engine import OCREngine

            with PDFExtractor(self.job.input_path) as extractor:
                image = extractor.render_page_image(page_num, dpi=self.ocr_dpi)
            ocr_engine = OCREngine.instance()
            text = ocr_engine.extract_text(image, lang_code=self.job.source_lang)
            if text.strip():
                self.log(f"OCR extracted text from scanned page {page_num + 1}.")
            return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed for page %d: %s", page_num, exc)
            self.log(f"OCR failed on page {page_num + 1}: {exc}")
            return ""

    # -- stage 2: output assembly ---------------------------------------------
    def _build_outputs(self) -> None:
        job = self.job
        out_dir = ensure_output_dir(job.job_id, job.output_dir)
        base_name = sanitize_filename(Path(job.original_filename).stem + "_translated")
        title = Path(job.original_filename).stem

        for fmt in job.output_formats:
            if self.cancel_event.is_set():
                return
            output_path = out_dir / f"{base_name}.{fmt}"
            self.log(f"Writing {fmt.upper()} output...")

            pages_iter = checkpoint_manager.read_pages(job.job_id)
            pages_sorted = iter(sorted(pages_iter, key=lambda t: t[0]))

            if fmt == "docx":
                document_builder.build_docx(pages_sorted, output_path, job.target_lang, title=title)
            elif fmt == "pdf":
                document_builder.build_pdf(pages_sorted, output_path, job.target_lang, title=title)
            elif fmt == "txt":
                document_builder.build_txt(pages_sorted, output_path)
            else:
                logger.warning("Unknown output format '%s', skipping.", fmt)
                continue

            job.output_paths[fmt] = str(output_path)
            self.log(f"Saved {fmt.upper()} -> {output_path.name}")

    # -- helpers ---------------------------------------------------------
    def _percent(self, current: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return min(100.0, round((current / total) * 100, 1))

    def _record_page_duration(self, duration: float) -> None:
        self._page_durations.append(duration)
        if len(self._page_durations) > config.PAGE_TIME_WINDOW:
            self._page_durations.pop(0)

    def _estimate_eta(self, current: int, total: int) -> Optional[float]:
        if not self._page_durations or current >= total:
            return 0.0 if current >= total else None
        avg = sum(self._page_durations) / len(self._page_durations)
        remaining = max(0, total - current)
        return round(avg * remaining, 1)

    @staticmethod
    def _format_eta(seconds: Optional[float]) -> str:
        if seconds is None:
            return "calculating..."
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {sec}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
