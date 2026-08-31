"""core/pdf_extractor.py — streaming, crash-resilient PDF text extraction.

Design goals (per spec):
  * Never load the whole document's text into memory at once — pages are
    yielded one at a time via a generator.
  * Support 1000+ page documents on a machine with 16GB RAM.
  * Handle corrupted pages gracefully: a single bad page is logged and
    skipped (yielded as empty text) rather than crashing the whole job.
  * Detect pages that are likely scanned images (no extractable text) so
    the OCR module can be invoked only when actually needed.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import fitz  # PyMuPDF

import config
from core.document_model import Block, blocks_to_plain_text
from services.logger_service import get_logger

logger = get_logger("pdf_extractor")


class PDFCorruptedError(Exception):
    """Raised when the PDF cannot be opened at all (not just one bad page)."""


@dataclass
class ExtractedPage:
    page_num: int  # 0-indexed
    text: str
    needs_ocr: bool
    had_error: bool = False
    error_message: Optional[str] = None
    # Structured view of the page: headings, list items, tables, images.
    # Empty when the extractor is running in plain-text mode, or when the
    # page carries no recoverable structure (e.g. a scanned page bound for
    # OCR). `text` stays populated either way, so anything that only wants
    # words keeps working unchanged.
    blocks: list[Block] = field(default_factory=list)


class PDFExtractor:
    """Wraps a single fitz.Document and exposes a streaming page iterator.

    Usage:
        with PDFExtractor(path) as extractor:
            for page in extractor.iter_pages():
                ...
    """

    def __init__(self, path: str, structured: bool = True,
                 assets_dir: Optional[Path] = None) -> None:
        self.path = path
        self.structured = structured
        self.assets_dir = Path(assets_dir) if assets_dir else None
        self._doc: Optional[fitz.Document] = None
        self._profile = None
        # Tracks images already emitted so a letterhead logo repeated on
        # every page appears once in the reflowed output.
        self._seen_images: set[str] = set()

    def __enter__(self) -> "PDFExtractor":
        try:
            self._doc = fitz.open(self.path)
        except Exception as exc:  # noqa: BLE001
            raise PDFCorruptedError(f"Could not open PDF '{self.path}': {exc}") from exc
        if self._doc.page_count == 0:
            self._doc.close()
            raise PDFCorruptedError("PDF has zero pages.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    @property
    def page_count(self) -> int:
        assert self._doc is not None, "PDFExtractor must be used as a context manager"
        return self._doc.page_count

    def iter_pages(self, start_page: int = 0) -> Iterator[ExtractedPage]:
        """Yields one ExtractedPage at a time starting at `start_page`
        (0-indexed). A page that fails to decode is yielded with
        had_error=True and empty text rather than raising, so a single
        damaged page never aborts the whole job.
        """
        assert self._doc is not None, "PDFExtractor must be used as a context manager"
        doc = self._doc

        for page_num in range(start_page, doc.page_count):
            try:
                page = doc.load_page(page_num)
                blocks = self._extract_blocks(page, page_num)
                if blocks:
                    text = blocks_to_plain_text(blocks)
                else:
                    text = page.get_text("text") or ""
                needs_ocr = self._looks_scanned(page, text)
                yield ExtractedPage(
                    page_num=page_num, text=text, needs_ocr=needs_ocr, blocks=blocks
                )
                # Explicitly drop the reference; large image-heavy pages can
                # otherwise keep decoded pixmaps alive longer than needed.
                del page
            except Exception as exc:  # noqa: BLE001
                logger.warning("Page %d in '%s' failed to extract: %s", page_num, self.path, exc)
                yield ExtractedPage(
                    page_num=page_num,
                    text="",
                    needs_ocr=False,
                    had_error=True,
                    error_message=str(exc),
                )

    def profile(self):
        """Document-wide layout measurements, computed once and cached.

        Structure has to be judged relative to the document it came from —
        a 9pt body and a 14pt body are both "normal" somewhere — so this
        samples the document before any page is classified.
        """
        from core.layout_extractor import profile_document

        if self._profile is None:
            self._profile = profile_document(self._doc)
        return self._profile

    def _extract_blocks(self, page: "fitz.Page", page_num: int) -> list[Block]:
        """Structured read of one page. Never raises: a page whose layout
        cannot be parsed falls back to plain text rather than failing the
        job, matching the resilience contract of the rest of this class.
        """
        if not self.structured:
            return []
        try:
            from core.layout_extractor import extract_page_blocks

            return extract_page_blocks(
                page,
                self.profile(),
                self.assets_dir,
                self._seen_images,
                is_first_page=(page_num == 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Structured extraction failed on page %d, falling back to plain text: %s",
                page_num, exc,
            )
            return []

    @staticmethod
    def _looks_scanned(page: "fitz.Page", text: str) -> bool:
        """Heuristic: a page with almost no extractable text but at least
        one embedded image is very likely a scanned page requiring OCR.
        """
        if len(text.strip()) >= config.MIN_TEXT_CHARS_FOR_NON_SCANNED_PAGE:
            return False
        try:
            has_image = len(page.get_images(full=True)) > 0
        except Exception:  # noqa: BLE001
            has_image = False
        return has_image or len(text.strip()) == 0

    def render_page_image(self, page_num: int, dpi: int = config.OCR_RENDER_DPI):
        """Renders a page to a PIL Image for OCR. Imported lazily to avoid
        requiring Pillow when OCR is never used.
        """
        from PIL import Image

        assert self._doc is not None
        page = self._doc.load_page(page_num)
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        del page
        return img
