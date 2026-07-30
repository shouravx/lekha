"""core/ocr_engine.py — optional OCR module using PaddleOCR.

Design goals:
  * Fully optional: if OCR is disabled in Settings, this module is never
    imported and PaddleOCR's (fairly heavy) model files are never loaded
    into memory — important on a machine with no dedicated GPU.
  * Lazy singleton: the PaddleOCR engine itself (model weights) is loaded
    once on first use and reused for every scanned page, since
    construction is the expensive part (disk I/O + model init).
  * Only invoked for pages PDFExtractor flagged as `needs_ocr` — normal
    text PDFs never touch this module at all.
  * On the target hardware (i3-10100, no dedicated GPU), this runs on
    CPU. PaddleOCR's mobile-detection models are deliberately chosen for
    speed over the heavier server models.
"""

from __future__ import annotations

import threading
from typing import Optional

import config
from services.logger_service import get_logger

logger = get_logger("ocr_engine")


class OCREngineUnavailableError(Exception):
    """Raised when OCR is requested but PaddleOCR isn't installed/usable."""


class OCREngine:
    """Lazy singleton wrapper around PaddleOCR.

    Usage:
        engine = OCREngine.instance()
        text = engine.extract_text(pil_image, lang="en")
    """

    _instance: Optional["OCREngine"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._engines: dict[str, object] = {}  # one PaddleOCR instance per language
        self._engines_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "OCREngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = OCREngine()
            return cls._instance

    def _get_engine(self, lang_code: str):
        paddle_lang = config.OCR_LANG_MAP.get(lang_code, "en")
        with self._engines_lock:
            engine = self._engines.get(paddle_lang)
            if engine is not None:
                return engine

            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise OCREngineUnavailableError(
                    "PaddleOCR is not installed. Install it with "
                    "`pip install paddleocr paddlepaddle`, or disable OCR in Settings."
                ) from exc

            logger.info("Loading PaddleOCR model for language '%s' (first use, may take a moment)...", paddle_lang)
            try:
                engine = PaddleOCR(
                    lang=paddle_lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception as exc:  # noqa: BLE001
                raise OCREngineUnavailableError(f"Failed to initialize PaddleOCR: {exc}") from exc

            self._engines[paddle_lang] = engine
            return engine

    def extract_text(self, image, lang_code: str = "en") -> str:
        """Runs OCR on a PIL Image and returns the recognized text, lines
        joined with newlines in reading order (top-to-bottom as returned
        by PaddleOCR's detector).
        """
        import numpy as np

        engine = self._get_engine(lang_code)
        image_array = np.array(image.convert("RGB"))

        try:
            results = engine.predict(image_array)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR prediction failed: %s", exc)
            return ""

        lines: list[str] = []
        for res in results:
            data = getattr(res, "json", None)
            texts = None
            if isinstance(data, dict):
                texts = data.get("res", {}).get("rec_texts")
            if texts is None and hasattr(res, "rec_texts"):
                texts = res.rec_texts  # fallback for older result shapes
            if texts:
                lines.extend(t for t in texts if t and t.strip())

        return "\n".join(lines)


def ocr_available() -> bool:
    """Cheap check used by the UI to grey out the OCR toggle if PaddleOCR
    isn't installed, without paying the cost of loading any models."""
    try:
        import paddleocr  # noqa: F401

        return True
    except ImportError:
        return False
