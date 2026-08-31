"""services/settings_service.py — persisted, user-editable app settings.
Defaults come from config.py; anything the user changes in the Settings
page is written to data/app_settings.json and overrides the defaults for
all subsequent sessions/jobs.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import config
from services.logger_service import get_logger

logger = get_logger("settings_service")

# Reentrant: update() holds the lock and then calls get_all(), which takes
# it again. With a plain Lock that deadlocks the calling thread forever —
# which in Streamlit means every "Save settings" button (and the Start
# Translation button, which persists tuning before submitting) hangs the
# app permanently.
_lock = threading.RLock()

DEFAULTS: dict[str, Any] = {
    "default_source_lang": "en",
    "default_target_lang": "bn",
    "default_output_formats": config.DEFAULT_OUTPUT_FORMATS,
    "output_dir": str(config.OUTPUTS_DIR),
    "chunk_max_chars": config.MAX_CHUNK_CHARS,
    "translation_workers": config.TRANSLATION_WORKERS,
    "ocr_enabled_default": config.OCR_ENABLED_DEFAULT,
    "ocr_dpi": config.OCR_RENDER_DPI,
    "max_file_size_mb": config.MAX_FILE_SIZE_MB,
    "accent_color": "violet",
    # Dark is the default because of the use scene, not by category habit:
    # this app is most often left running on a home machine overnight.
    "theme": "dark",
    "keep_checkpoints_after_completion": False,
    # --- Hybrid pipeline (both stages opt-in; defaults preserve the
    # fully-offline behaviour Lekha shipped with) ---------------------
    "translation_backend": config.DEFAULT_TRANSLATION_BACKEND,
    "online_chunk_max_chars": config.ONLINE_MAX_CHUNK_CHARS,
    "online_translation_workers": config.ONLINE_TRANSLATION_WORKERS,
    "refine_enabled_default": config.REFINE_ENABLED_DEFAULT,
    "ollama_base_url": config.OLLAMA_BASE_URL,
    "refine_model": config.REFINE_MODEL,
    "refine_block_chars": config.REFINE_BLOCK_CHARS,
    "refine_timeout": config.REFINE_TIMEOUT,
    # Start the managed Ollama server on demand when a job needs the
    # polish pass. Only ever starts a runtime that is already
    # installed; it never downloads anything on its own.
    "auto_start_ai": True,
}


class SettingsService:
    def __init__(self, path: Path = config.SETTINGS_FILE) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self._write(dict(DEFAULTS))

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_all(self) -> dict[str, Any]:
        with _lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            merged = {**DEFAULTS, **data}
            return merged

    def get(self, key: str, default: Any = None) -> Any:
        return self.get_all().get(key, default if default is not None else DEFAULTS.get(key))

    def update(self, **kwargs: Any) -> None:
        with _lock:
            current = self.get_all()
            current.update(kwargs)
            self._write(current)
        logger.info("Settings updated: %s", list(kwargs.keys()))

    def reset_to_defaults(self) -> None:
        with _lock:
            self._write(dict(DEFAULTS))
        logger.info("Settings reset to defaults")


settings_service = SettingsService()
