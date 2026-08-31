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
import warnings
from pathlib import Path

# requests emits a RequestsDependencyWarning when chardet's version falls
# outside the range it declares. chardet 7.x arrives here transitively and
# the mismatch is harmless in practice — requests prefers
# charset_normalizer, which is installed and in range. Left alone it
# printed on every launch, twice, and buried the launcher's actual output.
# Registered here because config is imported before anything reaches for
# requests, and matched by message so no other warning is hidden.
warnings.filterwarnings(
    "ignore",
    message=r".*doesn't match a supported version.*",
)

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
# Large downloaded runtimes (the managed Ollama install). Kept out of
# data/ so a multi-gigabyte binary never sits beside the small JSON
# files a user might reasonably want to copy or back up.
RUNTIME_DIR: Path = BASE_DIR / "runtime"

for _d in (UPLOADS_DIR, OUTPUTS_DIR, CHECKPOINTS_DIR, LOGS_DIR, DATA_DIR, FONTS_DIR,
           RUNTIME_DIR):
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

# A failed chunk falls back to the untranslated source text, which is the
# right call for one bad chunk and the wrong call for a backend that has
# stopped responding entirely. Past this many *consecutive* failures the
# job aborts rather than producing a confidently untranslated document.
MAX_CONSECUTIVE_CHUNK_FAILURES: int = 12

# ---------------------------------------------------------------------------
# Hybrid pipeline: online translation backend (opt-in)
# ---------------------------------------------------------------------------
# Lekha's default path is 100% offline (Argos). The "hybrid" path trades
# that privacy guarantee for speed: Google Translate does the heavy
# vocabulary lifting over HTTP (near-zero local CPU), and an optional
# small local LLM polishes the result. Both stages are off by default.
DEFAULT_TRANSLATION_BACKEND: str = "argos"  # "argos" | "google"

# The online backend is billed in HTTP round-trips, not CPU, so the
# chunking economics invert: 400-char chunks would mean ~30,000 requests
# for a 1000-page book and near-certain rate limiting. Send far more text
# per request instead. Google's endpoint accepts ~5000 chars; stay under.
ONLINE_MAX_CHUNK_CHARS: int = 3000
ONLINE_TRANSLATION_WORKERS: int = 2   # concurrent HTTP requests, kept low
ONLINE_MIN_REQUEST_INTERVAL: float = 0.15  # seconds between requests (global)
ONLINE_REQUEST_TIMEOUT: int = 30      # per-request timeout, seconds
ONLINE_MAX_RETRIES: int = 3           # retries on transient/rate-limit errors
ONLINE_RETRY_BACKOFF: float = 2.0     # exponential backoff base, seconds

# ---------------------------------------------------------------------------
# Hybrid pipeline: local LLM refinement (opt-in)
# ---------------------------------------------------------------------------
# A small instruct model (3B class) run through Ollama smooths machine
# translation into natural book prose. It only *edits* already-translated
# text, so a 3B model on CPU is sufficient — no GPU required.
REFINE_ENABLED_DEFAULT: bool = False
OLLAMA_BASE_URL: str = "http://localhost:11434"
REFINE_MODEL: str = "qwen2.5:3b"
REFINE_TIMEOUT: int = 120             # per-block timeout, seconds
REFINE_TEMPERATURE: float = 0.2       # low: we want edits, not invention
REFINE_KEEP_ALIVE: str = "30m"        # keep model resident across a long job
REFINE_MAX_RETRIES: int = 1

# Refinement is by far the slowest stage, and cost scales with the number
# of LLM calls. Translated chunks are re-joined into larger blocks before
# refining so one call covers several chunks' worth of prose.
REFINE_BLOCK_CHARS: int = 1200

# Blocks shorter than this are passed through unrefined. Headings and
# two-word list items have no prose to improve, and a small model is at
# its least reliable on fragments that short — it tends to answer them
# rather than edit them.
REFINE_MIN_BLOCK_CHARS: int = 25

# Guardrails — a small model can hallucinate, answer in the wrong
# language, or refuse. If refined output violates these bounds we discard
# it and keep the raw machine translation for that block.
REFINE_MIN_LENGTH_RATIO: float = 0.45  # output/input char ratio, lower bound
REFINE_MAX_LENGTH_RATIO: float = 2.50  # ... and upper bound
REFINE_MIN_SCRIPT_RETENTION: float = 0.50  # fraction of target-script density to keep

# Unicode ranges used to verify the refiner answered in the target script.
TARGET_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "bn": (0x0980, 0x09FF),  # Bengali
}

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
