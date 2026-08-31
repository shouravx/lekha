"""core/recheck.py — verifies the translation against the source.

This is the "recheck" half of the feedback loop. It cannot judge whether
a translation reads well — nothing automatic can, and pretending
otherwise would be worse than saying nothing. What it can do is check the
invariants that hold regardless of language, which is precisely the class
of failure that slipped through before:

    info@archtechbd.com   destroyed
    www.archtechbd.com    destroyed
    +880 1767-963535      a digit dropped

Every one of those is mechanically detectable without reading a word of
Bengali. Findings are reported, never silently repaired: an automatic fix
applied to text nobody checked is how one bad rule quietly rewrites a
whole document.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.document_model import Block, BlockKind
from core.protect import protected_spans
from services.logger_service import get_logger

logger = get_logger("recheck")

# A translation far shorter than its source usually means the engine gave
# up on a segment. Bengali runs longer than English rather than shorter,
# so this threshold is generous and still catches real collapses.
_MIN_LENGTH_RATIO = 0.35
# Below this many characters, length ratios are meaningless noise.
_MIN_LENGTH_CHECK_CHARS = 40


@dataclass
class Finding:
    kind: str
    page: int          # 1-based, for humans
    detail: str

    def __str__(self) -> str:
        return f"page {self.page}: {self.detail}"


def _text_of(blocks: list[Block]) -> str:
    return "\n".join(
        b.text for b in blocks
        if b.kind not in (BlockKind.IMAGE,) and b.text.strip()
    )


def check_page(page_num: int, source_blocks: list[Block],
               translated_blocks: list[Block], source_lang: str,
               target_lang: str) -> list[Finding]:
    """Compares one page's source and translation. Never raises."""
    try:
        return _check(page_num, source_blocks, translated_blocks, source_lang, target_lang)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recheck failed on page %d: %s", page_num, exc)
        return []


def _check(page_num, source_blocks, translated_blocks, source_lang, target_lang):
    findings: list[Finding] = []
    page = page_num + 1

    source_text = _text_of(source_blocks)
    translated_text = _text_of(translated_blocks)

    if not source_text.strip():
        return findings

    # 1. Protected spans must survive verbatim.
    for kind, values in protected_spans(source_text).items():
        for value in values:
            if value not in translated_text:
                findings.append(Finding(
                    kind=f"lost_{kind}",
                    page=page,
                    detail=f"{kind} not preserved: {value!r}",
                ))

    # 2. A page with source text must not come back empty.
    if not translated_text.strip():
        findings.append(Finding(
            kind="empty_output", page=page,
            detail="the page had text but produced no translation",
        ))
        return findings

    # 3. Whole blocks that came back unchanged. Identical output is
    #    expected for a block that is entirely protected content (a bare
    #    URL, a number), so those are excluded rather than reported.
    for src, out in zip(source_blocks, translated_blocks):
        s, o = src.text.strip(), out.text.strip()
        if not s or s != o or len(s) < 12:
            continue
        if protected_spans(s):
            continue
        if not any(ch.isalpha() for ch in s):
            continue
        findings.append(Finding(
            kind="untranslated", page=page,
            detail=f"left untranslated: {s[:60]!r}",
        ))

    # 4. A collapse in length.
    if len(source_text) >= _MIN_LENGTH_CHECK_CHARS:
        ratio = len(translated_text) / len(source_text)
        if ratio < _MIN_LENGTH_RATIO:
            findings.append(Finding(
                kind="short_output", page=page,
                detail=f"translation is {ratio:.0%} of the source length",
            ))

    return findings


def summarise(findings: list[Finding], pages: int) -> str:
    """One line for the job log."""
    if not findings:
        return f"Recheck: {pages} page(s), no issues found."

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    parts = ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in sorted(counts.items()))
    return f"Recheck: {pages} page(s), {len(findings)} issue(s) — {parts}."


def detail_lines(findings: list[Finding], limit: int = 12) -> list[str]:
    """The individual findings, capped so a badly broken job cannot flood
    the log with thousands of lines."""
    lines = [f"  · {f}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"  · ... and {len(findings) - limit} more")
    return lines
