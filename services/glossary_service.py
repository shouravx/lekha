"""services/glossary_service.py — the terms you have taught Lekha.

Two kinds of entry, and the distinction matters:

  keep     Never translate this. Brand names, product names, people.
           "ArchTech BD" must come out as "ArchTech BD", not as the
           model's phonetic guess at it.
  replace  Always translate this exactly so. "Tel:" became "তেল:" —
           Bengali for *oil* — because the model translated a label as a
           word. A fixed rendering removes the guess.

This is the part of the feedback loop that persists. It is deliberately a
plain, readable JSON file rather than anything learned: you can see every
rule, edit it, delete it, and know exactly why a term came out the way it
did. A model that quietly drifted toward your corrections would be
impossible to audit and impossible to undo.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import config
from services.logger_service import get_logger

logger = get_logger("glossary_service")
_lock = threading.RLock()

GLOSSARY_FILE: Path = config.DATA_DIR / "glossary.json"

KEEP = "keep"
REPLACE = "replace"


class GlossaryService:
    def __init__(self, path: Path = GLOSSARY_FILE) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self._write([])

    # -- storage ---------------------------------------------------------
    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read the glossary, treating it as empty: %s", exc)
            return []
        return data if isinstance(data, list) else []

    # -- public API -------------------------------------------------------
    def all_entries(self) -> list[dict[str, Any]]:
        """Every entry, sorted by source term."""
        with _lock:
            entries = self._read()
        return sorted(entries, key=lambda e: str(e.get("source", "")).lower())

    def for_pair(self, source_lang: str, target_lang: str) -> dict[str, dict]:
        """The lookup `core.protect` consumes: lowercased source term ->
        {"target", "mode"}.

        A `keep` entry applies to every language pair — a brand name is a
        brand name whatever you are translating into. A `replace` entry is
        only valid for the pair it was written for, since its target text
        is in one specific language.
        """
        table: dict[str, dict] = {}
        for entry in self.all_entries():
            source = str(entry.get("source", "")).strip()
            if not source:
                continue
            mode = entry.get("mode", KEEP)
            if mode == REPLACE:
                if entry.get("target_lang") not in (None, "", target_lang):
                    continue
                if not str(entry.get("target", "")).strip():
                    continue
            table[source.lower()] = {
                "target": entry.get("target", ""),
                "mode": mode,
            }
        return table

    def add(self, source: str, mode: str = KEEP, target: str = "",
            target_lang: str = "") -> bool:
        """Adds or updates a term. Returns False if `source` was blank."""
        source = (source or "").strip()
        if not source:
            return False
        mode = mode if mode in (KEEP, REPLACE) else KEEP

        with _lock:
            entries = self._read()
            entries = [
                e for e in entries
                if str(e.get("source", "")).strip().lower() != source.lower()
            ]
            entries.append({
                "source": source,
                "mode": mode,
                "target": (target or "").strip(),
                "target_lang": target_lang or "",
            })
            self._write(entries)
        logger.info("Glossary: %s '%s' (%s)", "set", source, mode)
        return True

    def remove(self, source: str) -> None:
        with _lock:
            entries = [
                e for e in self._read()
                if str(e.get("source", "")).strip().lower() != (source or "").strip().lower()
            ]
            self._write(entries)
        logger.info("Glossary: removed '%s'", source)

    def clear(self) -> None:
        with _lock:
            self._write([])
        logger.info("Glossary cleared")

    def count(self) -> int:
        return len(self.all_entries())


glossary_service = GlossaryService()
