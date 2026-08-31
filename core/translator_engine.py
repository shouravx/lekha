"""core/translator_engine.py — thin, thread-safe wrapper around Argos
Translate.

Design goals:
  * 100% offline at runtime. The only network access Argos Translate ever
    needs (`update_package_index` + `.download()`) happens in
    `scripts/download_models.py`, a separate one-time setup step the user
    runs once with internet access. The application itself never calls
    those functions.
  * Cheap to query "is this language pair installed?" so the UI can show
    a clear, friendly message instead of crashing when a model is missing.
  * Easy to extend: adding a new language pair is just adding a tuple to
    config.SUPPORTED_LANGUAGE_PAIRS and running the download script again
    — no code changes here.
  * Translation objects are loaded once and cached, since constructing
    them from installed packages has measurable overhead and a 1000+
    page job calls .translate() thousands of times.
"""

from __future__ import annotations

import threading
from typing import Optional

from services.logger_service import get_logger

logger = get_logger("translator_engine")


class TranslationBackendError(Exception):
    """Base class for 'this backend cannot do the job' failures.

    Raised for conditions the user can act on — a missing model, a missing
    package, a backend that has stopped responding — as opposed to bugs.
    The pipeline catches these to fail a job cleanly, with the exception's
    message shown to the user verbatim.
    """


class ModelNotInstalledError(TranslationBackendError):
    """Raised when a translation is requested for a language pair whose
    Argos Translate package has not been installed locally."""


class BackendUnresponsiveError(TranslationBackendError):
    """Raised when a backend that was working has started failing every
    request — a dropped connection or a revoked endpoint, rather than a
    single bad chunk."""


class ArgosTranslatorEngine:
    """Singleton facade over argostranslate.translate.

    Usage:
        engine = ArgosTranslatorEngine.instance()
        text = engine.translate("Hello", "en", "bn")
    """

    _instance: Optional["ArgosTranslatorEngine"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._translation_cache: dict[tuple[str, str], object] = {}
        self._cache_lock = threading.Lock()
        self._installed_pairs: Optional[set[tuple[str, str]]] = None
        self._installed_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ArgosTranslatorEngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = ArgosTranslatorEngine()
            return cls._instance

    # -- discovery ---------------------------------------------------------
    def _refresh_installed_pairs(self) -> set[tuple[str, str]]:
        """Inspects locally installed Argos packages. Does NOT touch the
        network — only reads packages already installed on disk.
        """
        pairs: set[tuple[str, str]] = set()
        try:
            # Imported inside the try so that argostranslate being absent
            # entirely reports "no pairs installed" — and the UI's friendly
            # message — rather than raising out of an availability *query*.
            # This is what makes an online-backend-only install viable.
            import argostranslate.translate as argos_translate

            installed_languages = argos_translate.get_installed_languages()
            for lang in installed_languages:
                for translation in getattr(lang, "translations_from", []):
                    pairs.add((lang.code, translation.to_lang.code))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to enumerate installed Argos languages: %s", exc)
        return pairs

    def is_pair_available(self, source_lang: str, target_lang: str) -> bool:
        if source_lang == target_lang:
            return True
        with self._installed_lock:
            if self._installed_pairs is None:
                self._installed_pairs = self._refresh_installed_pairs()
            return (source_lang, target_lang) in self._installed_pairs

    def refresh(self) -> None:
        """Forces a re-scan of installed packages (call after running the
        download script while the app is open, e.g. from Settings)."""
        with self._installed_lock:
            self._installed_pairs = self._refresh_installed_pairs()
        with self._cache_lock:
            self._translation_cache.clear()

    # -- translation ---------------------------------------------------------
    def _get_translation(self, source_lang: str, target_lang: str):
        key = (source_lang, target_lang)
        with self._cache_lock:
            cached = self._translation_cache.get(key)
            if cached is not None:
                return cached

        import argostranslate.translate as argos_translate

        translation = argos_translate.get_translation_from_codes(source_lang, target_lang)
        if translation is None:
            raise ModelNotInstalledError(
                f"No installed Argos Translate model for '{source_lang}' -> '{target_lang}'. "
                "Run `python scripts/download_models.py` once with an internet connection."
            )

        with self._cache_lock:
            self._translation_cache[key] = translation
        return translation

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translates a single chunk of text. Empty/whitespace-only input
        is returned unchanged (avoids wasting model calls on blank lines,
        which are common in page-extracted PDF text).
        """
        if not text or not text.strip():
            return text
        if source_lang == target_lang:
            return text

        translation = self._get_translation(source_lang, target_lang)
        try:
            return translation.translate(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Translation chunk failed (%d chars): %s", len(text), exc)
            raise


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
def resolve_engine(backend: str):
    """Returns the translation engine for `backend`.

    Both engines expose the same three methods the pipeline relies on —
    translate(), is_pair_available() and refresh() — so the pipeline holds
    whichever one it is given without branching on type.

    Falls back to the offline Argos engine on an unrecognised value: a bad
    setting should degrade to the private, always-available path rather
    than to an online one.
    """
    from models.enums import TranslationBackend

    try:
        selected = TranslationBackend(backend)
    except ValueError:
        logger.warning("Unknown translation backend '%s'; falling back to Argos.", backend)
        selected = TranslationBackend.ARGOS

    if selected is TranslationBackend.GOOGLE:
        from core.online_translator import GoogleTranslateEngine

        return GoogleTranslateEngine.instance()
    return ArgosTranslatorEngine.instance()
