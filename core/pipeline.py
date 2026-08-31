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
from core.translator_engine import (
    BackendUnresponsiveError,
    ModelNotInstalledError,
    TranslationBackendError,
    resolve_engine,
)
from models.enums import JobStatus, TranslationBackend
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

        settings = settings_service.get_all()
        self.ocr_dpi = settings.get("ocr_dpi", config.OCR_RENDER_DPI)

        try:
            self.backend = TranslationBackend(job.translation_backend)
        except ValueError:
            self.backend = TranslationBackend.ARGOS
        self.engine = resolve_engine(self.backend.value)

        # Chunking economics differ per backend. Argos is CPU-bound and
        # most accurate on short input; the online backend is bound by HTTP
        # round-trips, where small chunks mean thousands of requests and
        # near-certain rate limiting. Each gets its own tuning.
        if self.backend.is_online:
            self.max_chunk_chars = int(
                settings.get("online_chunk_max_chars", config.ONLINE_MAX_CHUNK_CHARS)
            )
            self.workers = max(
                1, int(settings.get("online_translation_workers", config.ONLINE_TRANSLATION_WORKERS))
            )
        else:
            self.max_chunk_chars = int(settings.get("chunk_max_chars", config.MAX_CHUNK_CHARS))
            self.workers = max(
                1, int(settings.get("translation_workers", config.TRANSLATION_WORKERS))
            )

        # Refinement is resolved in run() so an unreachable Ollama degrades
        # the job to plain translation instead of failing it.
        self.refine_enabled = bool(getattr(job, "refine_enabled", False))
        self.refine_block_chars = int(settings.get("refine_block_chars", config.REFINE_BLOCK_CHARS))
        self.refiner = None

        # Guards against a dead backend quietly producing an untranslated
        # document. Written from several worker threads, hence the lock.
        self._consecutive_chunk_failures = 0
        self._failure_lock = threading.Lock()

        self._page_durations: list[float] = []  # rolling window for ETA

    # -- public entrypoint ---------------------------------------------------
    def run(self) -> None:
        job = self.job
        job.started_at = job.started_at or datetime.now().isoformat(timespec="seconds")
        start_time = time.monotonic()

        try:
            self._preflight_engine()
            self._preflight_refiner()

            self.log(f"Starting translation of '{job.original_filename}'.")
            self.log(f"Translation backend: {self.backend.label}.")
            if self.backend.is_online:
                self.log(
                    "NOTE: this job's text is being sent to Google's servers for translation."
                )
            self._translate_pages()

            if self.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                self.log("Job cancelled by user.")
                self.progress(status=JobStatus.CANCELLED.value)
                return

            self._log_refinement_summary()

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

        except (PDFCorruptedError, TranslationBackendError) as exc:
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

    # -- preflight -----------------------------------------------------------
    def _preflight_engine(self) -> None:
        """Fails the job *before* any pages are processed if the selected
        backend can't work at all — far better than discovering it 400
        pages in."""
        job = self.job
        if self.engine.is_pair_available(job.source_lang, job.target_lang):
            return

        if self.backend.is_online:
            raise ModelNotInstalledError(
                "The online translation backend needs the deep-translator package. "
                "Install it with `pip install deep-translator`, or switch the backend "
                "back to Argos (offline) in Settings."
            )
        raise ModelNotInstalledError(
            f"Translation model '{job.source_lang}'->'{job.target_lang}' is not installed. "
            "Run `python scripts/download_models.py` once with internet access."
        )

    def _preflight_refiner(self) -> None:
        """Resolves the LLM refiner, if the job asked for one.

        An unreachable Ollama disables refinement for this job rather than
        failing it: losing the polish pass on a long document is a far
        better outcome than losing the translation with it. The downgrade
        is logged prominently so it is never silent.
        """
        if not self.refine_enabled:
            return

        from core.llm_refiner import LLMRefiner

        settings = settings_service.get_all()
        refiner = LLMRefiner.instance()
        refiner.configure(
            base_url=settings.get("ollama_base_url", config.OLLAMA_BASE_URL),
            model=settings.get("refine_model", config.REFINE_MODEL),
            timeout=int(settings.get("refine_timeout", config.REFINE_TIMEOUT)),
        )

        if not refiner.is_available(force=True):
            self.refine_enabled = False
            self.log(
                f"WARNING: LLM refinement was requested but Ollama is unreachable at "
                f"{refiner.base_url}. Continuing with unrefined machine translation. "
                "Start Ollama (`ollama serve`) and re-run to enable polishing."
            )
            return

        installed = refiner.list_models()
        if installed and refiner.model not in installed:
            self.refine_enabled = False
            self.log(
                f"WARNING: refinement model '{refiner.model}' is not installed in Ollama. "
                f"Pull it with `ollama pull {refiner.model}`. Continuing without refinement."
            )
            return

        refiner.reset_stats()
        self.refiner = refiner
        self.log(f"LLM refinement enabled using '{refiner.model}' via Ollama.")

    def _log_refinement_summary(self) -> None:
        if self.refiner is None:
            return
        refined, fallback = self.refiner.get_stats()
        total = refined + fallback
        if total == 0:
            return
        if fallback:
            self.log(
                f"Refinement: {refined}/{total} blocks polished. {fallback} block(s) kept "
                "the raw translation because the model's output failed validation."
            )
        else:
            self.log(f"Refinement: all {refined} block(s) polished successfully.")

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
                    "translation_backend": self.backend.value,
                    "refine_enabled": self.refine_enabled,
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

        if self.refine_enabled and self.refiner is not None:
            return self._refine_page(translated_chunks)

        return "\n".join(translated_chunks)

    def _refine_page(self, translated_chunks: list[str]) -> str:
        """Runs the polish pass over one page's translated chunks.

        Chunks are regrouped into larger blocks first — the refiner's cost
        is dominated by per-call overhead, and a bigger block also gives
        the editor more context to keep the prose consistent.
        """
        blocks = chunker.group_into_blocks(translated_chunks, self.refine_block_chars)
        if not blocks:
            return ""

        refined: list[str] = []
        for block in blocks:
            if self.cancel_event.is_set():
                # Cancellation is checked between blocks as well as between
                # pages: a slow model can spend minutes inside one page, and
                # the user shouldn't have to wait it out.
                refined.append(block)
                continue
            refined.append(self.refiner.refine(block, self.job.target_lang))
        return "\n".join(refined)

    def _translate_chunk_safe(self, chunk: str) -> str:
        try:
            translated = self.engine.translate(chunk, self.job.source_lang, self.job.target_lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chunk translation failed, keeping original text: %s", exc)
            self._note_chunk_failure(exc)
            return chunk
        with self._failure_lock:
            self._consecutive_chunk_failures = 0
        return translated

    def _note_chunk_failure(self, exc: Exception) -> None:
        """Aborts the job when the backend has clearly stopped working.

        Falling back to the untranslated source text is the right response
        to one bad chunk. It is the wrong response to a dropped network
        connection on the online backend, which would otherwise produce a
        complete, confident, entirely untranslated book. Past a threshold
        of consecutive failures we stop and report instead.
        """
        with self._failure_lock:
            self._consecutive_chunk_failures += 1
            failures = self._consecutive_chunk_failures
        if failures < config.MAX_CONSECUTIVE_CHUNK_FAILURES:
            return
        raise BackendUnresponsiveError(
            f"The {self.backend.label} backend failed on {failures} consecutive chunks — "
            "aborting so the output isn't silently left untranslated. "
            f"Last error: {exc}"
        )

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
