"""core/protect.py — keeps things out of the translator that must not
be translated.

The failures this exists to stop, all measured on a real document:

    Email: info@archtechbd.com   ->  ই- মেইল: তথ্য@ info: credit
    Tel: +880 1767-963535        ->  তেল: +৮৮০ ১৭৬-৯৬৩৫৩৫   (a digit lost)
    ArchTech BD                  ->  আর্কচেচ বিডি

An address, a phone number and a brand name are not language. Passing
them through a translation model can only damage them.

Why splitting rather than masking
---------------------------------
The obvious approach is to swap each protected span for a placeholder,
translate, then swap back. It does not work here, and that was measured
rather than assumed: across eleven placeholder schemes (⟦0⟧, 【0】, %%0%%,
#0#, X0X, private-use characters, bare numbers, {{0}}, <x0>) not one
survived Argos intact in every trial. The best managed three out of four,
and with three placeholders in a single sentence it dropped one entirely.
A masking scheme that silently loses its markers corrupts text in a new
way instead of preventing corruption.

So a segment is split around its protected spans and only the
translatable pieces are sent to the engine. Protected content never
reaches the model at all, which makes preservation total rather than
probable. The cost is that a sentence containing a protected span is
translated in fragments; that cost is real but small, because the spans
this protects are overwhelmingly surrounded by short labels ("Email:",
"Tel:", "Web:") rather than embedded mid-clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.logger_service import get_logger

logger = get_logger("protect")


@dataclass
class Segment:
    """One piece of a split segment."""

    text: str
    protected: bool = False
    # True when this piece is the remainder of a split rather than a
    # whole standalone segment. Fragments get the trailing-stop treatment
    # described in _affixes; unsplit text is left exactly as it was so
    # ordinary paragraphs translate no differently than before.
    fragment: bool = False
    # For a glossary hit in "replace" mode, the text to emit instead of
    # translating. None means "emit `text` unchanged".
    replacement: str | None = None

    @property
    def output(self) -> str:
        return self.text if self.replacement is None else self.replacement


# Order matters: the first pattern to claim a span owns it. URLs are
# matched before bare domains, and emails before either, so
# "info@archtechbd.com" is never split across two rules.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("url", re.compile(r"(?:https?://|ftp://|www\.)[^\s<>\"']+")),
    ("domain", re.compile(r"\b(?:[\w-]+\.)+(?:com|org|net|io|dev|bd|co|edu|gov)\b")),
    # A phone number: optional +, then digit groups with separators. Long
    # enough that it cannot collide with an ordinary number in prose —
    # "7 Partners" should still become "৭ Partners" in Bengali, so bare
    # small numbers are deliberately NOT protected.
    ("phone", re.compile(r"\+?\d[\d\s().-]{7,}\d")),
    ("path", re.compile(r"(?:[A-Za-z]:\\|\.{0,2}/)[\w\\/.:-]{3,}")),
    # Identifier-like tokens: version numbers, SKUs, file names.
    ("code", re.compile(r"\b[\w-]*\d[\w-]*\.[A-Za-z]{2,4}\b|\bv?\d+\.\d+(?:\.\d+)?\b")),
]


def _glossary_pattern(terms: list[str]) -> re.Pattern | None:
    """One alternation for every glossary term, longest first so
    'ArchTech BD Limited' wins over 'ArchTech BD'."""
    usable = sorted({t for t in terms if t and t.strip()}, key=len, reverse=True)
    if not usable:
        return None
    joined = "|".join(re.escape(t) for t in usable)
    # \b only anchors next to word characters, so a term starting or
    # ending in punctuation ("Tel:") would never match with it applied
    # unconditionally. Guard with lookarounds that tolerate either.
    return re.compile(rf"(?<!\w)(?:{joined})(?!\w)", re.IGNORECASE)


def split_protected(text: str, glossary: dict[str, dict] | None = None) -> list[Segment]:
    """Splits `text` into translatable and protected segments, in order.

    `glossary` maps a lowercased source term to
    {"target": str, "mode": "keep"|"replace"}.
    """
    if not text:
        return []

    glossary = glossary or {}
    spans: list[tuple[int, int, str | None]] = []  # (start, end, replacement)

    gloss_pattern = _glossary_pattern(list(glossary))
    if gloss_pattern is not None:
        for match in gloss_pattern.finditer(text):
            entry = glossary.get(match.group(0).lower())
            if entry is None:
                # Case-insensitive match against a differently-cased key.
                entry = next(
                    (v for k, v in glossary.items() if k == match.group(0).lower()), None
                )
            replacement = None
            if entry and entry.get("mode") == "replace" and entry.get("target"):
                replacement = entry["target"]
            spans.append((match.start(), match.end(), replacement))

    for _name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), None))

    if not spans:
        return [Segment(text=text)]

    # Resolve overlaps: earliest start wins, then longest. A glossary term
    # and a pattern claiming the same characters must not both emit.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    resolved: list[tuple[int, int, str | None]] = []
    cursor = 0
    for start, end, replacement in spans:
        if start < cursor:
            continue
        resolved.append((start, end, replacement))
        cursor = end

    segments: list[Segment] = []
    position = 0
    for start, end, replacement in resolved:
        if start > position:
            segments.append(Segment(text=text[position:start], fragment=True))
        segments.append(
            Segment(text=text[start:end], protected=True, replacement=replacement)
        )
        position = end
    if position < len(text):
        segments.append(Segment(text=text[position:], fragment=True))

    return segments


def has_protected(segments: list[Segment]) -> bool:
    return any(s.protected for s in segments)


# Sentence-final punctuation, Latin and Bengali.
_TRAILING_STOP = re.compile(r"[.!?।॥]+$")


def _affixes(text: str, fragment: bool = False) -> tuple[str, str, str]:
    """Splits leading whitespace, core, and trailing whitespace.

    For a fragment — a piece left over after splitting around a protected
    span — trailing sentence punctuation moves into the suffix as well.
    Measured: "provides professional engineering services." came back as
    "( হিতো.", while the identical words without the full stop translated
    correctly. A subjectless fragment ending in a full stop reads to the
    model as a complete sentence and derails it, so the stop is peeled off
    and put back afterwards.
    """
    core = text.strip()
    if not core:
        return ("", "", text)
    start = text.index(core[0])
    end = start + len(core)
    leading, trailing = text[:start], text[end:]

    if fragment:
        match = _TRAILING_STOP.search(core)
        if match and len(core) > len(match.group(0)):
            trailing = match.group(0) + trailing
            core = core[: match.start()]

    return (leading, core, trailing)


def translatable_cores(segments: list[Segment]) -> list[str]:
    """The strings that should actually be sent to the engine.

    Cores are stripped first. A fragment handed over as
    " provides professional engineering services." translated to garbage
    where the same words without the leading space did not — leading
    whitespace perturbs the tokenizer, and a split fragment starts with
    one far more often than a whole paragraph does.
    """
    cores = []
    for segment in segments:
        if segment.protected:
            continue
        _, core, _ = _affixes(segment.text, segment.fragment)
        if core:
            cores.append(core)
    return cores


def reassemble(segments: list[Segment], translations: list[str]) -> str:
    """Rebuilds the string from translated cores and untouched protected
    spans, restoring the whitespace the split removed.

    Whitespace is restored from the original rather than repaired
    afterwards with a regex. The regex version inserted a space at every
    position where the remainder matched an address pattern, which turned
    `info@archtechbd.com` into `i n f o@archtechbd.com` — each of `i`,
    `n`, `f` and `o` starts a valid match.
    """
    out: list[str] = []
    index = 0

    for segment in segments:
        if segment.protected:
            out.append(segment.output)
            continue

        leading, core, trailing = _affixes(segment.text, segment.fragment)
        if not core:
            out.append(segment.text)
            continue

        translated = translations[index] if index < len(translations) else core
        index += 1
        out.append(f"{leading}{translated.strip()}{trailing}")

    joined = "".join(out)

    # A split boundary can leave two pieces flush against each other when
    # the source had no whitespace there but the pieces are now separate
    # words. Only insert where both sides are word-like.
    joined = re.sub(r"(?<=[^\W\d_])(?=(?:https?://|www\.))", " ", joined)
    return re.sub(r"[ \t]{2,}", " ", joined).strip()


def protected_spans(text: str) -> dict[str, list[str]]:
    """Every protected span in `text`, by kind. Used by the recheck pass
    to state what must still be present afterwards."""
    found: dict[str, list[str]] = {}
    claimed: list[tuple[int, int]] = []
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < e and match.end() > s for s, e in claimed):
                continue
            claimed.append((match.start(), match.end()))
            found.setdefault(name, []).append(match.group(0))
    return found
