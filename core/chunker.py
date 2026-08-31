"""core/chunker.py — splits page text into translation-sized chunks.

Argos Translate (like most NMT engines) is most accurate and fastest on
sentence-length input. A page can contain a few thousand characters in
one block (dense academic text, no clean paragraph breaks), so naively
feeding the whole page to the translator would be slow and could exceed
the model's effective context. This module:

  * Splits on paragraph breaks first (cheap, preserves structure).
  * Within an over-long paragraph, splits on sentence boundaries.
  * As an absolute fallback (e.g. a single 5,000-character word-salad
    line with no punctuation), hard-splits on whitespace so we never
    produce a chunk larger than MAX_CHUNK_CHARS regardless of input.
  * Never splits a chunk smaller than necessary — keeps as much context
    as fits, which improves translation quality.
"""

from __future__ import annotations

import re
from typing import Iterator

import config

# Sentence boundary: punctuation (incl. Bengali daris \u0964 \u0965)
# followed by whitespace. Intentionally simple and fast — this runs over
# every page of a 1000+ page document.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\u0964\u0965])\s+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def split_into_chunks(text: str, max_chars: int = config.MAX_CHUNK_CHARS) -> list[str]:
    """Returns a list of text chunks, each at most ~max_chars long where
    possible, preserving sentence and paragraph boundaries.
    """
    if not text or not text.strip():
        return []

    chunks: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        chunks.extend(_chunk_paragraph(paragraph, max_chars))
    return chunks


def _chunk_paragraph(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]

    sentences = _SENTENCE_SPLIT_RE.split(paragraph)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > max_chars:
            # A single "sentence" is itself too long (e.g. no punctuation
            # at all) — flush what we have, then hard-split this sentence
            # on whitespace so no chunk ever exceeds max_chars.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(sentence, max_chars))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    words = text.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    # Absolute final fallback for a single "word" longer than max_chars
    # (e.g. a corrupted PDF run of repeated characters with no spaces).
    result = [c if len(c) <= max_chars else c[:max_chars] for c in chunks]
    return result or [text[:max_chars]]


def iter_chunks(text: str, max_chars: int = config.MAX_CHUNK_CHARS) -> Iterator[str]:
    """Generator variant of split_into_chunks for streaming consumers."""
    yield from split_into_chunks(text, max_chars)


def group_into_blocks(chunks: list[str], max_chars: int) -> list[str]:
    """Re-joins already-translated chunks into larger blocks.

    The inverse operation of split_into_chunks, used by the LLM refinement
    stage. Translation wants small chunks (accuracy, model context); the
    refiner wants large ones, because its cost is dominated by per-call
    overhead and a bigger block gives the editor more surrounding context
    to make the prose read consistently.

    Chunks are never split here — only concatenated — so a single chunk
    longer than max_chars becomes its own block rather than being cut.
    """
    if not chunks:
        return []

    blocks: list[str] = []
    current = ""
    for chunk in chunks:
        if not chunk or not chunk.strip():
            continue
        candidate = f"{current}\n{chunk}" if current else chunk
        if current and len(candidate) > max_chars:
            blocks.append(current)
            current = chunk
        else:
            current = candidate
    if current:
        blocks.append(current)
    return blocks
