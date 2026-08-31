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
from core.document_model import Block, BlockKind, Run, blocks_to_plain_text
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

        assets_dir = checkpoint_manager.assets_dir(job.job_id)
        with PDFExtractor(job.input_path, structured=True, assets_dir=assets_dir) as extractor:
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
                    translated_blocks = self._translate_one_page(page, pool)
                    translated_text = blocks_to_plain_text(translated_blocks)

                    checkpoint_manager.append_page(
                        job.job_id, page.page_num, translated_text, translated_blocks
                    )
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

    def _translate_one_page(self, page, pool: ThreadPoolExecutor) -> list[Block]:
        """Translates one page, preserving its structure.

        Returns Blocks rather than a string so headings, list items,
        tables and images survive translation and reach the output
        builders intact.
        """
        job = self.job
        blocks = list(page.blocks)

        if page.needs_ocr and job.ocr_enabled:
            ocr_text = self._ocr_page(page.page_num)
            if ocr_text.strip():
                # OCR yields plain text with no recoverable structure.
                blocks = _paragraphs_from_text(ocr_text)

        if not blocks:
            # Either structured extraction found nothing usable or the
            # extractor is in plain-text mode; fall back to the page's
            # text so a page is never silently dropped.
            blocks = _paragraphs_from_text(page.text)

        if not blocks:
            return []

        translated = self._translate_blocks(blocks, pool)

        if self.refine_enabled and self.refiner is not None:
            translated = self._refine_blocks(translated)

        return translated

    def _translate_blocks(self, blocks: list[Block], pool: ThreadPoolExecutor) -> list[Block]:
        """Translates every piece of text on a page in one batch.

        All translatable strings across all blocks are gathered first so
        the worker pool sees the whole page at once — translating block by
        block would serialise on the many short blocks (headings, list
        items) that structured extraction produces.
        """
        tasks: list[str] = []
        plan: list[tuple[str, int, object]] = []

        for index, block in enumerate(blocks):
            if block.kind is BlockKind.IMAGE:
                continue
            if block.kind is BlockKind.TABLE:
                for row_index, row in enumerate(block.rows):
                    for col_index, cell in enumerate(row):
                        if cell.strip():
                            plan.append(("cell", index, (row_index, col_index)))
                            tasks.append(cell)
                continue
            for seg_index, segment in enumerate(block.translation_segments()):
                if segment.text.strip():
                    plan.append(("seg", index, seg_index))
                    tasks.append(segment.text)

        if not tasks:
            return blocks

        if len(tasks) == 1:
            results = [self._translate_text_safe(tasks[0])]
        else:
            results = list(pool.map(self._translate_text_safe, tasks))

        seg_results: dict[int, dict[int, str]] = {}
        cell_results: dict[int, dict[tuple[int, int], str]] = {}
        for (kind, index, position), result in zip(plan, results):
            if kind == "seg":
                seg_results.setdefault(index, {})[position] = result
            else:
                cell_results.setdefault(index, {})[position] = result

        out: list[Block] = []
        for index, block in enumerate(blocks):
            if block.kind is BlockKind.IMAGE:
                out.append(block)
                continue
            if block.kind is BlockKind.TABLE:
                rows = [list(row) for row in block.rows]
                for (row_index, col_index), value in cell_results.get(index, {}).items():
                    rows[row_index][col_index] = value
                out.append(Block(kind=BlockKind.TABLE, rows=rows))
                continue
            segments = block.translation_segments()
            translated = [
                seg_results.get(index, {}).get(i, segments[i].text)
                for i in range(len(segments))
            ]
            out.append(block.with_translated_segments(translated))
        return out

    def _translate_text_safe(self, text: str) -> str:
        """Translates one string, chunking it first if it exceeds the
        backend's per-request size."""
        if len(text) <= self.max_chunk_chars:
            return self._translate_chunk_safe(text)
        chunks = chunker.split_into_chunks(text, max_chars=self.max_chunk_chars)
        if not chunks:
            return text
        return " ".join(self._translate_chunk_safe(chunk) for chunk in chunks)

    def _refine_blocks(self, blocks: list[Block]) -> list[Block]:
        """Runs the polish pass over a page's translated blocks.

        Refinement is per block rather than over the page as a whole: the
        model is asked to rewrite prose, and it cannot be relied on to
        preserve block boundaries, so feeding it several blocks at once
        would make it impossible to map its answer back onto the
        document's structure.

        Headings and short list items are skipped — there is nothing to
        polish in three words, and a small model is at its least reliable
        on fragments that short.
        """
        refined: list[Block] = []
        for block in blocks:
            if block.kind in (BlockKind.IMAGE, BlockKind.TABLE):
                refined.append(block)
                continue
            if block.kind in (BlockKind.TITLE, BlockKind.HEADING):
                refined.append(block)
                continue

            text = block.text
            if len(text.strip()) < config.REFINE_MIN_BLOCK_CHARS or self.cancel_event.is_set():
                # Cancellation is checked here as well as between pages: a
                # slow model can spend minutes on one page and the user
                # shouldn't have to wait it out.
                refined.append(block)
                continue

            if len(text) <= self.refine_block_chars:
                polished = self.refiner.refine(text, self.job.target_lang)
            else:
                pieces = chunker.split_into_chunks(text, max_chars=self.refine_block_chars)
                polished = " ".join(
                    self.refiner.refine(piece, self.job.target_lang) for piece in pieces
                )

            bold, italic = block.dominant_style
            refined.append(Block(
                kind=block.kind,
                runs=[Run(text=polished, bold=bold, italic=italic)],
                level=block.level,
                indent=block.indent,
                list_depth=block.list_depth,
            ))
        return refined

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

            if fmt == "txt":
                pages_sorted = iter(sorted(
                    checkpoint_manager.read_pages(job.job_id), key=lambda t: t[0]
                ))
                document_builder.build_txt(pages_sorted, output_path)
                job.output_paths[fmt] = str(output_path)
                self.log(f"Saved {fmt.upper()} -> {output_path.name}")
                continue

            # DOCX and PDF rebuild from the structured form, so headings,
            # lists, tables and images are reproduced rather than flattened.
            blocks_sorted = iter(sorted(
                checkpoint_manager.read_structured_pages(job.job_id), key=lambda t: t[0]
            ))

            if fmt == "docx":
                document_builder.build_docx(blocks_sorted, output_path, job.target_lang, title=title)
            elif fmt == "pdf":
                document_builder.build_pdf(blocks_sorted, output_path, job.target_lang, title=title)
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


def _paragraphs_from_text(text: str) -> list[Block]:
    """Wraps plain text in paragraph blocks.

    The fallback path for pages with no recoverable structure — OCR
    output, or a layout the extractor could not parse — so such a page
    still flows through the structured pipeline instead of vanishing.
    """
    if not text or not text.strip():
        return []
    return [
        Block(kind=BlockKind.PARAGRAPH, runs=[Run(text=line.strip())])
        for line in text.splitlines()
        if line.strip()
    ]
