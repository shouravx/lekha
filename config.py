"""
config.py
=========
Central configuration for Lekha.

All paths, defaults, and tunables live here so the rest of the
application never hard-codes a magic value. Settings entered by the
user in the Settings page are persisted separately (see
services/settings_service.py) and override the defaults declared here.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent

UPLOADS_DIR: Path = BASE_DIR / "uploads"
OUTPUTS_DIR: Path = BASE_DIR / "outputs"
CHECKPOINTS_DIR: Path = BASE_DIR / "checkpoints"
LOGS_DIR: Path = BASE_DIR / "logs"
ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
DATA_DIR: Path = BASE_DIR / "data"  # history.json, app_settings.json

for _d in (UPLOADS_DIR, OUTPUTS_DIR, CHECKPOINTS_DIR, LOGS_DIR, DATA_DIR, FONTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Persisted data files
# ---------------------------------------------------------------------------
HISTORY_FILE: Path = DATA_DIR / "history.json"
SETTINGS_FILE: Path = DATA_DIR / "app_settings.json"
APP_LOG_FILE: Path = LOGS_DIR / "app.log"

# ---------------------------------------------------------------------------
# Fonts (bundled, Apache-2.0 licensed Noto fonts -> safe to redistribute)
# ---------------------------------------------------------------------------
BENGALI_FONT_PATH: Path = FONTS_DIR / "NotoSansBengali-Regular.ttf"
BENGALI_FONT_NAME: str = "notosansbengali"

# Windows ships with "Nirmala UI" which natively renders Bengali/Indic
# scripts in Word. We prefer it for DOCX output on the target machine.
DOCX_FONT_LATIN: str = "Calibri"
DOCX_FONT_BENGALI: str = "Nirmala UI"

# ---------------------------------------------------------------------------
# Supported languages (extend this dict to add more language pairs later)
# Keys are ISO-639-1 codes used by both Argos Translate and the UI.
# ---------------------------------------------------------------------------
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "bn": "Bengali",
}

# Language pairs the app actively ships translation models for.
# Add tuples here (and download the matching Argos package) to support
# more directions without touching any other code.
SUPPORTED_LANGUAGE_PAIRS: list[tuple[str, str]] = [
    ("en", "bn"),
    ("bn", "en"),
]

# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------
MAX_FILE_SIZE_MB: int = 1024  # hard ceiling on accepted upload size
MIN_TEXT_CHARS_FOR_NON_SCANNED_PAGE: int = 25  # below this -> treat as scanned
OCR_RENDER_DPI: int = 200

# ---------------------------------------------------------------------------
# Chunking & translation performance
# ---------------------------------------------------------------------------
MAX_CHUNK_CHARS: int = 400          # max characters per translation chunk
TRANSLATION_WORKERS: int = 2        # bounded thread pool for chunk translation
PAGE_TIME_WINDOW: int = 12          # rolling window (pages) used for ETA calc
CHECKPOINT_FLUSH_EVERY_PAGE: int = 1  # write checkpoint after every N pages

# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------
OUTPUT_FORMATS: list[str] = ["docx", "pdf", "txt"]
DEFAULT_OUTPUT_FORMATS: list[str] = ["docx"]

# ---------------------------------------------------------------------------
# OCR (optional, lazy-loaded — see core/ocr_engine.py)
# ---------------------------------------------------------------------------
OCR_ENABLED_DEFAULT: bool = False
OCR_LANG_MAP: dict[str, str] = {
    # Maps our ISO codes -> PaddleOCR language codes.
    "en": "en",
    "bn": "bn",
}

# ---------------------------------------------------------------------------
# Queue / threading
# ---------------------------------------------------------------------------
MAX_CONCURRENT_JOBS: int = 1  # process jobs strictly one-at-a-time (low-end HW)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
APP_NAME: str = "Lekha"
APP_TAGLINE: str = "Offline PDF Translator"
APP_VERSION: str = "1.0.0"


def get_default_output_dir() -> Path:
    """Returns the directory where finished translations are written."""
    return OUTPUTS_DIR


def is_windows() -> bool:
    return os.name == "nt"
