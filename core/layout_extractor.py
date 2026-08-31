"""core/layout_extractor.py — reads a PDF page as *structure*, not text.

PDFExtractor.iter_pages() calls page.get_text("text"), which returns a
bare string. Everything that makes a document look like a document —
heading sizes, bold, bullets, indentation, tables, images — is discarded
at that moment, before translation has even started. This module reads
the same pages through page.get_text("dict") instead, which exposes the
per-span font, size, weight and position that the flat call throws away,
and turns them into the Block model the builders can rebuild from.

How the classification works
----------------------------
PDFs have no notion of "heading" or "list item" — only glyphs at
coordinates. Structure has to be inferred, and the inference is
relative to each document rather than to fixed thresholds, because a
document whose body text is 9pt and one whose body is 14pt should both
be read correctly:

  * A profiling pre-pass finds the document's *body* font size (the most
    common size by character count) and its left text margin.
  * Text noticeably larger than body, or bold and short and unpunctuated,
    is a heading. Explicit section numbering ("2.", "2.1", "2.1.3")
    overrides size, since it states the depth outright.
  * A line beginning with a bullet glyph — or with a span in a symbol
    font — is a list item, and its indent gives the nesting depth.
  * Text found inside a detected table's bounds is emitted as part of
    that table rather than twice.

Running headers and footers
---------------------------
Repeating a letterhead and a phone number on all 8 pages is right in a
paged PDF and wrong in a reflowed document, where it lands mid-sentence
every few paragraphs. The profiling pass records text and images that
recur in the top/bottom margins across pages, and the extractor emits
them once, on the page where they first appear.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.document_model import Block, BlockKind, Run
from services.logger_service import get_logger

logger = get_logger("layout_extractor")

# PyMuPDF span flag bits.
_FLAG_ITALIC = 1 << 1
_FLAG_BOLD = 1 << 4

_BULLET_CHARS = set("•◦▪‣∙·◆●○■□–—-*")
_SYMBOL_FONT_HINTS = ("symbol", "wingding", "zapf", "dingbat")

# "2." / "2.1" / "2.1.3" at the start of a line states its own depth.
_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s*(?=\S)")
# A heading rarely ends in sentence punctuation.
_SENTENCE_END_RE = re.compile(r"[.!?;:,।॥]\s*$")

# Fractions of page height treated as the header / footer margins.
_HEADER_ZONE = 0.12
_FOOTER_ZONE = 0.88
# A line must recur on at least this share of sampled pages to count as
# a running header/footer.
_RUNNING_THRESHOLD = 0.6
# Headings are short. Anything longer than this is prose, whatever its font.
_MAX_HEADING_CHARS = 120

# Minimum vector primitives on a page before it is worth running the
# (expensive) table finder. A ruled table needs a grid; a page with only a
# header rule has two or three primitives and cannot produce one.
_MIN_DRAWINGS_FOR_TABLE = 5


@dataclass
class DocumentProfile:
    """Document-wide measurements that individual pages are judged against."""

    body_size: float = 12.0
    left_margin: float = 72.0
    page_width: float = 612.0
    page_height: float = 792.0
    title_size: float = 0.0
    running_texts: set[str] = field(default_factory=set)

    def size_ratio(self, size: float) -> float:
        return size / self.body_size if self.body_size else 1.0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _span_bold(span: dict[str, Any]) -> bool:
    if span.get("flags", 0) & _FLAG_BOLD:
        return True
    font = str(span.get("font", "")).lower()
    return "bold" in font or "black" in font or "heavy" in font


def _span_italic(span: dict[str, Any]) -> bool:
    if span.get("flags", 0) & _FLAG_ITALIC:
        return True
    font = str(span.get("font", "")).lower()
    return "italic" in font or "oblique" in font


def _is_bullet_span(span: dict[str, Any]) -> bool:
    text = span.get("text", "").strip()
    if not text or len(text) > 2:
        return False
    if text[0] in _BULLET_CHARS:
        return True
    font = str(span.get("font", "")).lower()
    return any(hint in font for hint in _SYMBOL_FONT_HINTS)


def profile_document(doc, max_sample_pages: int = 24) -> DocumentProfile:
    """Measures body size, margin and running headers across a sample.

    Sampling rather than reading every page keeps this O(1) on a
    1000-page document while still being representative — the first
    pages plus an even spread through the rest.
    """
    total = doc.page_count
    if total <= max_sample_pages:
        sample = list(range(total))
    else:
        head = list(range(min(8, total)))
        step = max(1, total // (max_sample_pages - len(head)))
        spread = list(range(len(head), total, step))[: max_sample_pages - len(head)]
        sample = sorted(set(head + spread))

    sizes: Counter[float] = Counter()
    x_starts: Counter[float] = Counter()
    margin_texts: Counter[str] = Counter()
    page_width = 612.0
    page_height = 792.0
    max_size = 0.0

    for page_num in sample:
        try:
            page = doc.load_page(page_num)
            page_width = page.rect.width
            page_height = page.rect.height
            data = page.get_text("dict")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Profiling skipped page %d: %s", page_num, exc)
            continue

        header_limit = page_height * _HEADER_ZONE
        footer_limit = page_height * _FOOTER_ZONE
        seen_on_page: set[str] = set()

        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                line_text = "".join(s["text"] for s in spans)
                y0 = line.get("bbox", [0, 0, 0, 0])[1]
                x0 = line.get("bbox", [0, 0, 0, 0])[0]

                for span in spans:
                    if _is_bullet_span(span):
                        continue
                    size = round(float(span.get("size", 0)), 1)
                    weight = len(span["text"].strip())
                    sizes[size] += weight
                    max_size = max(max_size, size)
                x_starts[round(x0)] += 1

                if y0 <= header_limit or y0 >= footer_limit:
                    key = _norm(line_text)
                    # A bare number in the margin is a page number: it
                    # differs on every page, so frequency can't catch it.
                    # Very short fragments are excluded outright — a lone
                    # bullet glyph near a page bottom recurs on most pages
                    # and would otherwise be classified as a running
                    # footer, silently deleting list items.
                    if len(key) >= 4 and not key.replace(" ", "").isdigit():
                        seen_on_page.add(key)

        for key in seen_on_page:
            margin_texts[key] += 1

    body_size = sizes.most_common(1)[0][0] if sizes else 12.0
    left_margin = min(x_starts) if x_starts else 72.0
    threshold = max(2, int(len(sample) * _RUNNING_THRESHOLD))
    running = {t for t, n in margin_texts.items() if n >= threshold}

    profile = DocumentProfile(
        body_size=body_size,
        left_margin=float(left_margin),
        page_width=page_width,
        page_height=page_height,
        title_size=max_size,
        running_texts=running,
    )
    logger.info(
        "Document profile: body=%.1fpt margin=%.0f title=%.1fpt running_header_lines=%d",
        profile.body_size, profile.left_margin, profile.title_size, len(running),
    )
    return profile


class _LineInfo:
    """One extracted line, with the measurements needed to classify it."""

    __slots__ = ("runs", "size", "bold", "x0", "y0", "text", "is_bullet",
                 "block_index", "bullet_only")

    def __init__(self, runs: list[Run], size: float, bold: bool, x0: float, y0: float,
                 is_bullet: bool, block_index: int = 0, bullet_only: bool = False) -> None:
        self.runs = runs
        self.size = size
        self.bold = bold
        self.x0 = x0
        self.y0 = y0
        self.is_bullet = is_bullet
        self.block_index = block_index
        self.bullet_only = bullet_only
        self.text = "".join(r.text for r in runs)

    @property
    def wrap_signature(self) -> tuple:
        # Lines merge into one paragraph only when they agree on size,
        # weight, indent and source block — an x0 bucket of 6pt absorbs
        # the sub-point jitter typical of justified text.
        #
        # Deliberately excludes is_bullet: the second and later lines of a
        # wrapped list item carry no marker of their own, but must still
        # join the item rather than becoming loose paragraphs.
        return (round(self.size, 1), self.bold, round(self.x0 / 6), self.block_index)


def _line_from_spans(spans: list[dict[str, Any]], bbox: list[float],
                     block_index: int = 0) -> Optional[_LineInfo]:
    spans = [s for s in spans if s.get("text", "").strip()]
    if not spans:
        return None

    # Consume any leading marker spans. Doing this by prefix rather than
    # by testing only spans[0] handles both layouts: the marker inline
    # with its text, and the marker alone on its own line.
    lead = 0
    while lead < len(spans) and _is_bullet_span(spans[lead]):
        lead += 1
    is_bullet = lead > 0
    content = spans[lead:]

    if not content:
        # The line holds nothing but a marker. Many generators lay the
        # bullet out as its own line — often its own block — at a smaller
        # indent than the text it introduces, so it is kept to be attached
        # to the following line rather than dropped.
        return _LineInfo(
            runs=[], size=float(spans[0].get("size", 0)), bold=False,
            x0=float(bbox[0]), y0=float(bbox[1]), is_bullet=False,
            block_index=block_index, bullet_only=True,
        )

    runs = [
        Run(text=s["text"], bold=_span_bold(s), italic=_span_italic(s))
        for s in content
    ]
    if not "".join(r.text for r in runs).strip():
        return None

    sizes = [float(s.get("size", 0)) for s in content]
    size = max(sizes) if sizes else 0.0
    bold_chars = sum(len(s["text"]) for s in content if _span_bold(s))
    total_chars = max(1, sum(len(s["text"]) for s in content))
    x0 = content[0].get("bbox", bbox)[0] if content[0].get("bbox") else bbox[0]

    return _LineInfo(
        runs=runs,
        size=size,
        bold=bold_chars / total_chars > 0.6,
        x0=float(x0),
        y0=float(bbox[1]),
        is_bullet=is_bullet,
        block_index=block_index,
    )


def _sort_reading_order(lines: list[_LineInfo]) -> list[_LineInfo]:
    """Orders lines top-to-bottom, then left-to-right within each row.

    Sorting on raw y0 is not enough. Items sharing a visual row rarely
    share an exact top: a 10pt bullet glyph and the 12pt text beside it
    differ by a fraction of a point, and that fraction is enough to sort
    the marker *after* its own text. Lines are therefore clustered into
    rows within a tolerance first, and ordered by x only inside a row.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda l: l.y0)
    rows: list[list[_LineInfo]] = []
    current: list[_LineInfo] = []
    row_top = None

    for line in ordered:
        tolerance = max(3.0, line.size * 0.6)
        if row_top is None or abs(line.y0 - row_top) <= tolerance:
            if row_top is None:
                row_top = line.y0
            current.append(line)
        else:
            rows.append(current)
            current = [line]
            row_top = line.y0
    if current:
        rows.append(current)

    out: list[_LineInfo] = []
    for row in rows:
        row.sort(key=lambda l: l.x0)
        out.extend(row)
    return out


def _attach_orphan_bullets(lines: list[_LineInfo]) -> list[_LineInfo]:
    """Attaches marker-only lines to the text line they introduce.

    Word and many PDF generators emit the bullet glyph as its own line —
    sometimes its own block — indented less than the item text. Without
    this pass every bullet list in the document degrades into a run of
    unmarked paragraphs.
    """
    attached: list[_LineInfo] = []
    pending: Optional[_LineInfo] = None

    for line in lines:
        if line.bullet_only:
            pending = line
            continue
        if pending is not None:
            # Only adopt the marker if the text starts on the same visual
            # row — otherwise it belongs to something else entirely.
            same_row = abs(line.y0 - pending.y0) <= max(4.0, pending.size)
            if same_row and line.x0 >= pending.x0:
                line.is_bullet = True
            pending = None
        attached.append(line)

    return attached


def _heading_level(text: str, size: float, bold: bool, profile: DocumentProfile) -> int:
    """Returns a heading level (1-based), or 0 if this isn't a heading."""
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return 0

    ratio = profile.size_ratio(size)

    # Explicit numbering states the depth outright and is the most
    # reliable signal available — but only when the line also looks like
    # a heading (bold or larger than body), so numbered body sentences
    # and list entries aren't promoted.
    match = _NUMBERED_HEADING_RE.match(stripped)
    if match and (bold or ratio >= 1.1):
        remainder = stripped[match.end():].strip()
        if remainder and not _SENTENCE_END_RE.search(stripped):
            depth = match.group(1).count(".") + 1
            return min(depth, 4)

    if ratio >= 1.4:
        return 1
    if ratio >= 1.18:
        return 2
    if bold and ratio >= 0.95 and not _SENTENCE_END_RE.search(stripped):
        # Bold, body-sized and short: a run-in heading. Require it to be
        # meaningfully shorter than a line of prose to avoid catching an
        # entirely-bold paragraph.
        if len(stripped) <= 70:
            return 3
    return 0


def _list_depth(x0: float, profile: DocumentProfile) -> int:
    indent = max(0.0, x0 - profile.left_margin)
    return min(4, int(indent // 24))


def extract_page_blocks(page, profile: DocumentProfile, assets_dir: Optional[Path] = None,
                        seen_images: Optional[set[str]] = None,
                        is_first_page: bool = False) -> list[Block]:
    """Reads one page into a list of Blocks, in reading order."""
    data = page.get_text("dict")
    page_height = page.rect.height or profile.page_height
    header_limit = page_height * _HEADER_ZONE
    footer_limit = page_height * _FOOTER_ZONE

    table_regions, table_items = _extract_tables(page)
    items: list[tuple[float, float, Block]] = list(table_items)

    # Lines are gathered across every text block on the page before being
    # grouped, because a bullet marker and the text it introduces are
    # frequently emitted as separate blocks. block_index is carried along
    # so unrelated blocks are still never merged into one paragraph.
    lines: list[_LineInfo] = []
    furniture: list[tuple[BlockKind, list[Run]]] = []

    for block_index, raw_block in enumerate(data.get("blocks", [])):
        bbox = raw_block.get("bbox", [0, 0, 0, 0])
        if _inside_any(bbox, table_regions):
            continue

        if raw_block.get("type") == 1:
            image_block = _image_block(
                raw_block, bbox, page_height, assets_dir, seen_images, page.number
            )
            if image_block is not None:
                items.append((bbox[1], bbox[0], image_block))
            continue

        for raw_line in raw_block.get("lines", []):
            info = _line_from_spans(
                raw_line.get("spans", []), raw_line.get("bbox", bbox), block_index
            )
            if info is None:
                continue

            in_margin = info.y0 <= header_limit or info.y0 >= footer_limit
            if in_margin and info.text.strip().replace(" ", "").isdigit():
                continue  # bare page number

            # Letterhead and footer lines that repeat throughout the
            # document are page furniture, not body content. They are
            # captured once, from the first page, and routed to the
            # output's own header/footer rather than being interleaved
            # into the prose every few pages.
            if in_margin and _norm(info.text) in profile.running_texts:
                if is_first_page:
                    kind = BlockKind.HEADER if info.y0 <= header_limit else BlockKind.FOOTER
                    furniture.append((kind, _clean_runs(info.runs)))
                continue

            lines.append(info)

    lines = _attach_orphan_bullets(_sort_reading_order(lines))

    for y0, x0, block in _lines_to_blocks(lines, profile):
        items.append((y0, x0, block))

    items.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    blocks = [block for _, _, block in items if not block.is_empty()]

    # Page furniture leads the page's blocks so the builders can lift it
    # into the output's header/footer before laying out any body content.
    furniture_blocks = [
        Block(kind=kind, runs=runs) for kind, runs in furniture if runs
    ]
    return furniture_blocks + blocks


def _lines_to_blocks(lines: list[_LineInfo], profile: DocumentProfile) -> list[tuple[float, float, Block]]:
    """Groups consecutive lines into paragraphs and classifies each."""
    out: list[tuple[float, float, Block]] = []
    i = 0
    while i < len(lines):
        first = lines[i]
        level = _heading_level(first.text, first.size, first.bold, profile)

        # A heading is its own block: merging it with the prose beneath
        # would bury it.
        if level and not first.is_bullet:
            kind = BlockKind.TITLE if (
                level == 1 and profile.size_ratio(first.size) >= 1.4
            ) else BlockKind.HEADING
            out.append((first.y0, first.x0, Block(
                kind=kind,
                runs=_clean_runs(first.runs),
                level=level,
                indent=max(0.0, first.x0 - profile.left_margin),
            )))
            i += 1
            continue

        # Otherwise absorb following lines that share the same signature —
        # these are the wrapped continuation of one paragraph.
        group = [first]
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.wrap_signature != first.wrap_signature:
                break
            if nxt.is_bullet:
                break  # each bullet starts a new item
            if _heading_level(nxt.text, nxt.size, nxt.bold, profile):
                break
            group.append(nxt)
            j += 1

        runs: list[Run] = []
        for k, line in enumerate(group):
            if k:
                runs.append(Run(text=" "))
            runs.extend(line.runs)

        out.append((first.y0, first.x0, Block(
            kind=BlockKind.LIST_ITEM if first.is_bullet else BlockKind.PARAGRAPH,
            runs=_clean_runs(runs),
            indent=max(0.0, first.x0 - profile.left_margin),
            list_depth=_list_depth(first.x0, profile) if first.is_bullet else 0,
        )))
        i = j

    return out


def _clean_runs(runs: list[Run]) -> list[Run]:
    """Collapses whitespace across runs without losing run boundaries."""
    cleaned: list[Run] = []
    for run in runs:
        text = re.sub(r"[ \t ]+", " ", run.text)
        if not text:
            continue
        cleaned.append(Run(text=text, bold=run.bold, italic=run.italic))
    if cleaned:
        cleaned[0] = Run(text=cleaned[0].text.lstrip(), bold=cleaned[0].bold,
                         italic=cleaned[0].italic)
        cleaned[-1] = Run(text=cleaned[-1].text.rstrip(), bold=cleaned[-1].bold,
                          italic=cleaned[-1].italic)
    return [r for r in cleaned if r.text]


def _extract_tables(page) -> tuple[list[list[float]], list[tuple[float, float, Block]]]:
    regions: list[list[float]] = []
    blocks: list[tuple[float, float, Block]] = []

    # find_tables() is by far the most expensive call in extraction —
    # ~52ms/page against ~3ms to read the page's text, i.e. roughly two
    # thirds of the total, or nearly a minute of pure table-hunting on a
    # 1000-page book. Gate it on a cheap check first (~2.5ms/page).
    #
    # The default detection strategy looks for ruling lines, so a page
    # without enough vector primitives to form a grid cannot yield a table
    # anyway and the call is pure cost. A page carrying a real table has
    # dozens of primitives; a page with just a header rule has two.
    try:
        if len(page.get_drawings()) < _MIN_DRAWINGS_FOR_TABLE:
            return regions, blocks
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not inspect drawings on page %s: %s", page.number, exc)

    try:
        finder = page.find_tables()
        tables = list(getattr(finder, "tables", finder))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Table detection unavailable on page %s: %s", page.number, exc)
        return regions, blocks

    for table in tables:
        try:
            rows = table.extract()
            bbox = list(table.bbox)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not extract a table on page %s: %s", page.number, exc)
            continue
        cleaned = [
            [(cell or "").strip().replace("\n", " ") for cell in row]
            for row in rows
            if any((cell or "").strip() for cell in row)
        ]
        if not cleaned:
            continue
        regions.append(bbox)
        blocks.append((bbox[1], bbox[0], Block(kind=BlockKind.TABLE, rows=cleaned)))
    return regions, blocks


def _inside_any(bbox: list[float], regions: list[list[float]]) -> bool:
    if not regions:
        return False
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for r in regions:
        if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
            return True
    return False


def _image_block(raw_block: dict[str, Any], bbox: list[float], page_height: float,
                 assets_dir: Optional[Path], seen_images: Optional[set[str]],
                 page_number: int) -> Optional[Block]:
    if assets_dir is None:
        return None
    payload = raw_block.get("image")
    if not payload:
        return None

    digest = hashlib.sha1(payload).hexdigest()[:16]

    # A logo repeated in every page's letterhead should appear once in a
    # reflowed document, not once per page.
    in_margin = bbox[1] <= page_height * _HEADER_ZONE or bbox[3] >= page_height * _FOOTER_ZONE
    if seen_images is not None:
        if digest in seen_images and in_margin:
            return None
        seen_images.add(digest)

    ext = raw_block.get("ext", "png") or "png"
    path = assets_dir / f"img_{digest}.{ext}"
    if not path.exists():
        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError as exc:
            logger.warning("Could not write extracted image on page %s: %s", page_number, exc)
            return None

    return Block(
        kind=BlockKind.IMAGE,
        image_path=str(path),
        image_width=float(bbox[2] - bbox[0]),
        image_height=float(bbox[3] - bbox[1]),
    )
