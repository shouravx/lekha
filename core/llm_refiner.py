"""core/llm_refiner.py — optional local-LLM polish stage.

What it does
------------
Machine translation produces text that is *correct* but reads like
machine translation: literal idioms, English word order, inconsistent
register. This stage hands each translated block to a small instruct
model (3B class, served by Ollama) and asks it to rewrite the block as
natural book prose in the target language.

Why a 3B model is enough
------------------------
The model is never asked to translate. It only edits text that has
already been translated, which is a far easier task than generating a
translation from scratch — so a 3B model on CPU is sufficient and no GPU
is required.

The honest cost
---------------
This is the slowest stage in the pipeline by a wide margin. A CPU-bound
3B model generates on the order of 5-15 tokens/second, so refining a
full-length book measurably multiplies total job time versus translation
alone. It is off by default and is best applied to documents where prose
quality matters more than throughput. Two things keep the cost bounded:
translated chunks are re-joined into larger blocks so one call covers
several chunks (config.REFINE_BLOCK_CHARS), and calls are serialised —
concurrent requests to one CPU-bound model thrash rather than parallelise.

Never trust the model
---------------------
A small model can drop content, answer in English, wrap its reply in
commentary, or refuse outright. Refinement is strictly best-effort: every
response is sanitised and then validated against length and target-script
bounds, and anything that fails is discarded in favour of the raw machine
translation. A refiner that is misbehaving or offline degrades the job to
plain translation — it never fails it, and never silently corrupts a page.
"""

from __future__ import annotations

import re
import threading
from typing import Optional

import config
from services.logger_service import get_logger

logger = get_logger("llm_refiner")

# Reasoning-tuned models emit a visible scratchpad before their answer.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```$", re.DOTALL)

# Conversational preambles a small instruct model adds despite being told
# not to ("Here is the corrected text:", "Sure! Corrected version:", ...).
_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure|okay|ok|certainly|here(?:'s| is)|below is|corrected|revised|output|result|"
    r"polished|edited|final)\b[^\n]{0,120}:\s*$",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "You are a professional book editor. You edit already-translated prose so it reads "
    "naturally, as if originally written by a native author. You never translate, never "
    "summarise, never add information, never remove information, and never comment on your "
    "work. You reply with the edited text and nothing else."
)

_USER_PROMPT_TEMPLATE = """Below is a rough machine-translated {language} passage from a book.

Rewrite it so it reads as natural, standard literary {language} ({register}). Fix grammar, word order, and awkward literal phrasing. Keep every fact, name, number and sentence of the original — do not shorten, expand, or explain. Keep the paragraph structure exactly as given.

Reply with the corrected {language} text only. Do not add any preamble, notes, or quotation marks.

Rough {language}:
{text}"""

# Per-language notes appended to the prompt. Bengali book prose is
# conventionally written in Shuddho Bhasha (সাধু/প্রমিত standard register)
# rather than colloquial speech, which is what the raw MT tends toward.
_LANGUAGE_REGISTER: dict[str, str] = {
    "bn": "Shuddho Bhasha — standard written Bengali, not colloquial speech",
    "en": "standard written English",
}


class RefinerUnavailableError(Exception):
    """Raised when refinement is requested but Ollama cannot be reached."""


def _script_density(text: str, lang_code: str) -> Optional[float]:
    """Fraction of non-whitespace characters that belong to lang_code's
    script. Returns None when we have no range defined for the language,
    in which case the script check is skipped rather than guessed at.
    """
    bounds = config.TARGET_SCRIPT_RANGES.get(lang_code)
    if bounds is None:
        return None
    low, high = bounds
    considered = [c for c in text if not c.isspace()]
    if not considered:
        return 0.0
    in_script = sum(1 for c in considered if low <= ord(c) <= high)
    return in_script / len(considered)


def _sanitize(raw: str) -> str:
    """Strips the scaffolding small models wrap around their answers."""
    text = _THINK_BLOCK_RE.sub("", raw).strip()

    fenced = _CODE_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    # Drop a leading conversational preamble line, but only if there is
    # actual content behind it — otherwise we would return nothing.
    lines = text.split("\n")
    while len(lines) > 1 and _PREAMBLE_RE.match(lines[0]):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    text = "\n".join(lines).strip()

    # Unwrap a whole-response quotation.
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        inner = text[1:-1].strip()
        if inner:
            text = inner

    return text.strip()


class LLMRefiner:
    """Singleton Ollama client for the refinement stage.

    Usage:
        refiner = LLMRefiner.instance()
        if refiner.is_available():
            polished = refiner.refine(text, "bn")
    """

    _instance: Optional["LLMRefiner"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self.base_url = str(config.OLLAMA_BASE_URL).rstrip("/")
        self.model = str(config.REFINE_MODEL)
        self.timeout = int(config.REFINE_TIMEOUT)
        self._available: Optional[bool] = None
        self._available_lock = threading.Lock()
        # A CPU-bound local model gains nothing from concurrent requests —
        # they contend for the same cores and inflate every latency. One
        # request at a time, process-wide.
        self._call_lock = threading.Lock()
        # Best-effort telemetry surfaced in the job log.
        self.refined_count = 0
        self.fallback_count = 0
        self._stats_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LLMRefiner":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = LLMRefiner()
            return cls._instance

    def configure(self, base_url: str = "", model: str = "", timeout: int = 0) -> None:
        """Applies user settings. Changing the endpoint invalidates the
        cached availability probe."""
        changed = False
        if base_url and base_url.rstrip("/") != self.base_url:
            self.base_url = base_url.rstrip("/")
            changed = True
        if model and model != self.model:
            self.model = model
            changed = True
        if timeout:
            self.timeout = int(timeout)
        if changed:
            with self._available_lock:
                self._available = None

    def reset_stats(self) -> None:
        with self._stats_lock:
            self.refined_count = 0
            self.fallback_count = 0

    def get_stats(self) -> tuple[int, int]:
        with self._stats_lock:
            return self.refined_count, self.fallback_count

    def _record(self, refined: bool) -> None:
        with self._stats_lock:
            if refined:
                self.refined_count += 1
            else:
                self.fallback_count += 1

    # -- availability -------------------------------------------------------
    def list_models(self) -> list[str]:
        """Model names currently pulled into the local Ollama instance."""
        try:
            import requests
        except ImportError:
            return []
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not list Ollama models: %s", exc)
            return []
        return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]

    def is_available(self, force: bool = False) -> bool:
        """True if Ollama is reachable. Cached, because this is consulted
        once per job rather than once per block."""
        with self._available_lock:
            if self._available is not None and not force:
                return self._available

        available = bool(self.list_models()) or self._ping()
        with self._available_lock:
            self._available = available
        return available

    def _ping(self) -> bool:
        try:
            import requests
        except ImportError:
            return False
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def self_test(self, target_lang: str = "bn") -> tuple[bool, str]:
        """Round-trips one short block so the Settings page can prove the
        whole refinement path works before a job depends on it."""
        try:
            import requests  # noqa: F401
        except ImportError:
            return False, "The 'requests' package is not installed."

        if not self._ping():
            return False, (
                f"Could not reach Ollama at {self.base_url}. "
                "Start it with 'ollama serve' and make sure the URL is correct."
            )

        models = self.list_models()
        if models and self.model not in models:
            return False, (
                f"Ollama is running but model '{self.model}' is not installed. "
                f"Pull it with 'ollama pull {self.model}'. Installed: {', '.join(models)}"
            )

        sample = "সে বলল যে সে আগামীকাল আসিবে না কারণ তার শরীর ভালো নেই।"
        try:
            result = self._generate(sample, target_lang)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if not result:
            return False, "The model returned an empty response."
        return True, result

    # -- refinement -----------------------------------------------------------
    def _generate(self, text: str, target_lang: str) -> str:
        """One raw Ollama call. Returns sanitised text, unvalidated."""
        import requests

        language = config.SUPPORTED_LANGUAGES.get(target_lang, target_lang)
        register = _LANGUAGE_REGISTER.get(target_lang, f"standard written {language}")
        prompt = _USER_PROMPT_TEMPLATE.format(language=language, register=register, text=text)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "stream": False,
            # keep_alive holds the model in RAM between blocks; without it
            # Ollama can unload mid-job and pay the load cost repeatedly.
            "keep_alive": config.REFINE_KEEP_ALIVE,
            "options": {
                "temperature": float(config.REFINE_TEMPERATURE),
                "top_p": 0.9,
            },
        }

        with self._call_lock:
            response = requests.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
            )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise RefinerUnavailableError(str(body["error"]))
        return _sanitize(body.get("response", ""))

    def _is_plausible(self, original: str, candidate: str, target_lang: str) -> tuple[bool, str]:
        """Rejects responses that can't be a faithful edit of the input."""
        if not candidate:
            return False, "empty response"

        ratio = len(candidate) / max(1, len(original))
        if ratio < config.REFINE_MIN_LENGTH_RATIO:
            return False, f"output too short (ratio {ratio:.2f})"
        if ratio > config.REFINE_MAX_LENGTH_RATIO:
            return False, f"output too long (ratio {ratio:.2f})"

        before = _script_density(original, target_lang)
        after = _script_density(candidate, target_lang)
        if before is not None and after is not None and before > 0.3:
            # The input was solidly in the target script; the edit must be
            # too. This is what catches a model that answers in English or
            # explains itself instead of editing.
            if after < before * config.REFINE_MIN_SCRIPT_RETENTION:
                return False, f"wrong script (density {before:.2f} -> {after:.2f})"

        return True, ""

    def refine(self, text: str, target_lang: str) -> str:
        """Polishes one block. Best-effort by contract: on any failure —
        unreachable server, timeout, implausible output — the original
        text is returned unchanged and the job continues.
        """
        if not text or not text.strip():
            return text

        attempts = int(config.REFINE_MAX_RETRIES) + 1
        for attempt in range(attempts):
            try:
                candidate = self._generate(text, target_lang)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Refinement call failed (attempt %d/%d, %d chars): %s",
                    attempt + 1,
                    attempts,
                    len(text),
                    exc,
                )
                continue

            ok, reason = self._is_plausible(text, candidate, target_lang)
            if ok:
                self._record(refined=True)
                return candidate

            logger.warning(
                "Discarded refined block (attempt %d/%d): %s", attempt + 1, attempts, reason
            )

        self._record(refined=False)
        return text


def refiner_available() -> bool:
    """Cheap check for the UI — does not raise if Ollama is absent."""
    return LLMRefiner.instance().is_available()
