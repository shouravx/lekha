"""services/logger_service.py — centralized logging.

Every module calls `get_logger(__name__)` to obtain a configured logger.
Logs are written both to console (useful when launched from a terminal)
and to a rotating log file under logs/app.log so issues on a 1000+ page
overnight run can be diagnosed after the fact.
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
from pathlib import Path

import config

_lock = threading.Lock()
_configured = False


def _configure_root() -> None:
    global _configured
    with _lock:
        if _configured:
            return
        root = logging.getLogger("lekha")
        root.setLevel(logging.INFO)

        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.handlers.RotatingFileHandler(
            config.APP_LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        console_handler.setLevel(logging.INFO)

        root.addHandler(file_handler)
        root.addHandler(console_handler)
        root.propagate = False
        _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(f"lekha.{name}")


def export_logs(destination: Path) -> Path:
    """Copies the current log file to `destination` for the user to download."""
    import shutil

    destination = Path(destination)
    if config.APP_LOG_FILE.exists():
        shutil.copy(config.APP_LOG_FILE, destination)
    else:
        destination.write_text("No logs recorded yet.\n", encoding="utf-8")
    return destination
