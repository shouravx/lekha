"""core/document_model.py — the structured representation of a document.

Why this exists
---------------
Lekha originally passed a bare `str` from extraction through translation
to output. That is lossless for words and catastrophic for everything
else: headings, bold, bullets, tables, images and indentation were all
discarded at extraction time, so the DOCX builder had nothing left to
rebuild from and emitted uniform 11pt paragraphs. Translated documents
came out looking like a text dump of the original rather than a copy of
it.

This module defines the shape that now flows through the whole pipeline
instead — extract -> translate -> checkpoint -> build — so formatting
survives the round trip.

Design notes
------------
* Everything is JSON-serialisable. The resume system appends one JSON
  line per page to pages.jsonl, so blocks must round-trip through
  `to_dict`/`from_dict` exactly.
* `Run` carries character formatting; `Block` carries paragraph
  formatting. This mirrors how both DOCX and PDF think about documents,
  which keeps the builders simple.
* Images are referenced by path, not embedded, so a 1000-page scan-heavy
  document doesn't balloon the checkpoint file or force the whole
  document's images into memory at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BlockKind(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    IMAGE = "image"
    # Page furniture — a letterhead or footer line that repeats on every
    # page of the source. Correct in a paged PDF, wrong in the body of a
    # reflowed document, where it would land between paragraphs every few
    # pages. Builders route these to the output's own header/footer.
    HEADER = "header"
    FOOTER = "footer"


@dataclass
class Run:
    """A span of text sharing one set of character formatting."""

    text: str
    bold: bool = False
    italic: bool = False

    @property
    def style_key(self) -> tuple[bool, bool]:
        return (self.bold, self.italic)

    def to_dict(self) -> dict[str, Any]:
        # Flags are omitted when false — this file is written once per
        # page for the length of a long job, so keeping lines compact
        # matters more than self-description here.
        d: dict[str, Any] = {"t": self.text}
        if self.bold:
            d["b"] = 1
        if self.italic:
            d["i"] = 1
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Run":
        return Run(text=d.get("t", ""), bold=bool(d.get("b")), italic=bool(d.get("i")))


@dataclass
class Block:
    """One paragraph-level element of the document."""

    kind: BlockKind = BlockKind.PARAGRAPH
    runs: list[Run] = field(default_factory=list)
    level: int = 0             # heading depth, 1-based; 0 for non-headings
    indent: float = 0.0        # left indent in points, relative to the text margin
    list_depth: int = 0        # nesting depth for list items
    rows: list[list[str]] = field(default_factory=list)  # TABLE only
    image_path: str = ""       # IMAGE only — absolute path on disk
    image_width: float = 0.0   # IMAGE only — natural width in points
    image_height: float = 0.0

    # -- text access -------------------------------------------------------
    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    def is_empty(self) -> bool:
        if self.kind is BlockKind.IMAGE:
            return not self.image_path
        if self.kind is BlockKind.TABLE:
            return not any(any(c.strip() for c in row) for row in self.rows)
        return not self.text.strip()

    @property
    def dominant_style(self) -> tuple[bool, bool]:
        """The formatting that best represents the block as a whole.

        Used when a block has to be translated as one unit and can only
        be re-emitted with a single style — weighted by character count so
        one stray bold word doesn't bold an entire paragraph.
        """
        bold_chars = sum(len(r.text) for r in self.runs if r.bold)
        italic_chars = sum(len(r.text) for r in self.runs if r.italic)
        total = max(1, sum(len(r.text) for r in self.runs))
        return (bold_chars / total > 0.5, italic_chars / total > 0.5)

    # -- translation helpers ------------------------------------------------
    def merged_runs(self) -> list[Run]:
        """Adjacent runs sharing formatting collapsed into one.

        PDF extraction routinely splits a single visual word across
        several spans; translating those separately would shred the text.
        """
        merged: list[Run] = []
        for run in self.runs:
            if merged and merged[-1].style_key == run.style_key:
                merged[-1] = Run(
                    text=merged[-1].text + run.text,
                    bold=run.bold,
                    italic=run.italic,
                )
            else:
                merged.append(Run(text=run.text, bold=run.bold, italic=run.italic))
        return merged

    def translation_segments(self) -> list[Run]:
        """The units this block should be translated in.

        Two competing goals: translation quality wants the largest
        possible unit (a fragment translates badly), while formatting
        fidelity wants each differently-styled run kept separate.

        The compromise: keep runs separate only when each is substantial
        enough to translate well on its own. A short run — a stray bold
        word mid-sentence — is folded into the block and the block is
        translated whole, accepting the loss of that one inline accent in
        exchange for a sentence that reads correctly.
        """
        merged = self.merged_runs()
        if len(merged) <= 1:
            return merged
        if all(len(r.text.strip()) >= _MIN_STANDALONE_SEGMENT for r in merged):
            return merged
        bold, italic = self.dominant_style
        return [Run(text=self.text, bold=bold, italic=italic)]

    def with_translated_segments(self, translated: list[str]) -> "Block":
        """Returns a copy of this block carrying translated text, with all
        paragraph-level formatting preserved."""
        segments = self.translation_segments()
        runs = [
            Run(text=new_text, bold=seg.bold, italic=seg.italic)
            for seg, new_text in zip(segments, translated)
        ]
        return Block(
            kind=self.kind,
            runs=runs,
            level=self.level,
            indent=self.indent,
            list_depth=self.list_depth,
            rows=self.rows,
            image_path=self.image_path,
            image_width=self.image_width,
            image_height=self.image_height,
        )

    # -- serialisation -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"k": self.kind.value}
        if self.runs:
            d["r"] = [r.to_dict() for r in self.runs]
        if self.level:
            d["l"] = self.level
        if self.indent:
            d["x"] = round(self.indent, 1)
        if self.list_depth:
            d["d"] = self.list_depth
        if self.rows:
            d["tb"] = self.rows
        if self.image_path:
            d["img"] = self.image_path
            d["iw"] = round(self.image_width, 1)
            d["ih"] = round(self.image_height, 1)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Block":
        try:
            kind = BlockKind(d.get("k", "paragraph"))
        except ValueError:
            kind = BlockKind.PARAGRAPH
        return Block(
            kind=kind,
            runs=[Run.from_dict(r) for r in d.get("r", [])],
            level=int(d.get("l", 0)),
            indent=float(d.get("x", 0.0)),
            list_depth=int(d.get("d", 0)),
            rows=d.get("tb", []) or [],
            image_path=d.get("img", ""),
            image_width=float(d.get("iw", 0.0)),
            image_height=float(d.get("ih", 0.0)),
        )


# A run shorter than this is treated as an inline accent rather than an
# independently translatable unit. Roughly "a couple of words".
_MIN_STANDALONE_SEGMENT = 6


def blocks_to_json(blocks: list[Block]) -> list[dict[str, Any]]:
    return [b.to_dict() for b in blocks]


def blocks_from_json(raw: list[dict[str, Any]]) -> list[Block]:
    return [Block.from_dict(d) for d in raw or []]


def blocks_to_plain_text(blocks: list[Block]) -> str:
    """Flattens blocks back to plain text.

    Used by the TXT builder, and as the bridge that lets structured pages
    be consumed by anything still expecting the old string-per-page shape.
    """
    lines: list[str] = []
    for block in blocks:
        if block.kind in (BlockKind.IMAGE, BlockKind.HEADER, BlockKind.FOOTER):
            continue
        if block.kind is BlockKind.TABLE:
            for row in block.rows:
                lines.append("\t".join(cell.strip() for cell in row))
            continue
        text = block.text.strip()
        if not text:
            continue
        if block.kind is BlockKind.LIST_ITEM:
            lines.append(f"{'  ' * block.list_depth}• {text}")
        else:
            lines.append(text)
    return "\n".join(lines)
