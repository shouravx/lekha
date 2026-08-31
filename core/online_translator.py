"""core/online_translator.py — the online (Google Translate) backend of
the hybrid pipeline.

Why this exists
---------------
Argos runs a full neural translation model on the CPU for every chunk.
That is what makes Lekha offline, and also what makes it slow on low-end
hardware. Google Translate does the same work in an HTTP round-trip with
essentially zero local compute — so on a machine with no GPU, the online
backend is dramatically faster.

The cost is privacy: every chunk of the document is sent to Google. This
module is therefore never reached unless the user explicitly selects the
online backend for a job. Argos remains the default everywhere.

Why deep-translator and not googletrans
---------------------------------------
googletrans talks to an undocumented endpoint and its releases break
whenever Google changes it (the widely-copied 4.0.0-rc1 pin is a symptom
of exactly that). deep-translator is actively maintained, exposes the
same one-line API, and — usefully here — also fronts DeepL, MyMemory and
LibreTranslate, so swapping providers later is a constructor change
rather than a rewrite.

Rate limiting
-------------
The free Google endpoint throttles aggressive callers. Two defences:
  * a process-wide minimum interval between requests, enforced under a
    lock so it holds across all worker threads, and
  * bounded exponential-backoff retries that specifically recognise
    rate-limit responses.
Combined with the much larger chunk size the pipeline uses on this path
(config.ONLINE_MAX_CHUNK_CHARS), a full book stays well inside what the
endpoint tolerates.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import config
from services.logger_service import get_logger

logger = get_logger("online_translator")

# Google's endpoint rejects payloads beyond ~5000 characters. The pipeline
# already chunks well below this; this is a last-resort guard so an
# oversized chunk degrades into several requests instead of an exception.
_HARD_CHAR_LIMIT = 4800

_RATE_LIMIT_MARKERS = ("429", "too many requests", "rate limit", "quota")


class OnlineTranslatorUnavailableError(Exception):
    """Raised when the online backend is selected but cannot be used —
    deep-translator isn't installed, or the network is unreachable."""


def deep_translator_installed() -> bool:
    """True if the optional deep-translator dependency is importable."""
    try:
        import deep_translator  # noqa: F401
    except ImportError:
        return False
    return True


class GoogleTranslateEngine:
    """Singleton facade over deep_translator.GoogleTranslator.

    Deliberately mirrors ArgosTranslatorEngine's surface (translate,
    is_pair_available, refresh) so core/pipeline.py can hold either one
    behind the same name.
    """

    _instance: Optional["GoogleTranslateEngine"] = None
    _instance_lock = threading.Lock()

    # Rate limiting is process-wide, not per-instance: two jobs must not
    # be able to double the request rate by holding separate engines.
    _throttle_lock = threading.Lock()
    _last_request_at: float = 0.0

    def __init__(self) -> None:
        self._min_interval = float(config.ONLINE_MIN_REQUEST_INTERVAL)
        self._max_retries = int(config.ONLINE_MAX_RETRIES)
        self._backoff = float(config.ONLINE_RETRY_BACKOFF)

    @classmethod
    def instance(cls) -> "GoogleTranslateEngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = GoogleTranslateEngine()
            return cls._instance

    # -- discovery ---------------------------------------------------------
    def is_pair_available(self, source_lang: str, target_lang: str) -> bool:
        """Google covers every pair Lekha exposes, so availability here is
        purely a question of whether the dependency is installed. Actual
        connectivity is proven by self_test() or the first request, not
        guessed at.
        """
        if source_lang == target_lang:
            return True
        return deep_translator_installed()

    def refresh(self) -> None:
        """No local model state to invalidate. Present so the engine stays
        interchangeable with ArgosTranslatorEngine."""
        return None

    def self_test(self) -> tuple[bool, str]:
        """Performs one tiny live translation to confirm the endpoint is
        reachable. Used by the Settings page's 'Test connection' button so
        the user finds out here rather than 400 pages into a job."""
        if not deep_translator_installed():
            return False, "deep-translator is not installed (pip install deep-translator)."
        try:
            result = self.translate("Hello", "en", "bn")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if not result or not result.strip():
            return False, "Google returned an empty response."
        return True, result.strip()

    # -- translation ---------------------------------------------------------
    def _throttle(self) -> None:
        """Blocks until at least _min_interval has elapsed since the last
        request started, across every thread in the process."""
        if self._min_interval <= 0:
            return
        with GoogleTranslateEngine._throttle_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - GoogleTranslateEngine._last_request_at)
            if wait > 0:
                time.sleep(wait)
            GoogleTranslateEngine._last_request_at = time.monotonic()

    def _translate_once(self, text: str, source_lang: str, target_lang: str) -> str:
        from deep_translator import GoogleTranslator

        # GoogleTranslator's constructor performs no I/O, so building one
        # per call costs nothing measurable next to the HTTP round-trip and
        # sidesteps any question of sharing instances across threads.
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        self._throttle()
        result = translator.translate(text)
        return result or ""

    def _translate_with_retry(self, text: str, source_lang: str, target_lang: str) -> str:
        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                return self._translate_once(text, source_lang, target_lang)
            except ImportError as exc:
                # A missing dependency will never resolve itself — fail fast
                # rather than burning the retry budget on it.
                raise OnlineTranslatorUnavailableError(
                    "deep-translator is not installed. Run 'pip install deep-translator', "
                    "or switch the translation backend back to Argos (offline)."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self._max_retries:
                    break
                rate_limited = any(m in str(exc).lower() for m in _RATE_LIMIT_MARKERS)
                delay = self._backoff ** (attempt + 1)
                if rate_limited:
                    delay *= 2  # back off harder when explicitly throttled
                logger.warning(
                    "Google translate attempt %d/%d failed (%s). Retrying in %.1fs.",
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)

        raise OnlineTranslatorUnavailableError(
            f"Google Translate request failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translates one chunk. Mirrors ArgosTranslatorEngine.translate:
        blank input and same-language requests short-circuit without any
        network call."""
        if not text or not text.strip():
            return text
        if source_lang == target_lang:
            return text

        if len(text) <= _HARD_CHAR_LIMIT:
            return self._translate_with_retry(text, source_lang, target_lang)

        # Oversized chunk: split on whitespace and translate the pieces.
        parts: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= _HARD_CHAR_LIMIT:
                parts.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, _HARD_CHAR_LIMIT)
            if split_at <= 0:
                split_at = _HARD_CHAR_LIMIT
            parts.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()

        logger.info(
            "Chunk of %d chars exceeded endpoint limit; split into %d requests.",
            len(text),
            len(parts),
        )
        return " ".join(
            self._translate_with_retry(part, source_lang, target_lang) for part in parts
        )
