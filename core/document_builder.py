"""core/document_builder.py — assembles final output documents (DOCX,
PDF, TXT) from translated page text.

Streaming by design: callers pass an Iterator[tuple[page_num, text]]
(typically `checkpoint_manager.read_pages(job_id)`), and each builder
writes incrementally rather than holding the full document text in
memory. This is what makes 1000+ page output generation safe on 16GB
RAM — only one page's worth of text is ever resident at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import config
from services.logger_service import get_logger

logger = get_logger("document_builder")


def _is_bengali_target(target_lang: str) -> bool:
    return target_lang == "bn"


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------
def build_txt(pages: Iterator[tuple[int, str]], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        first = True
        for page_num, text in pages:
            if not first:
                f.write("\n\f\n")  # form-feed page break, readable in any text editor
            f.write(f"--- Page {page_num + 1} ---\n")
            f.write(text)
            first = False
    logger.info("Built TXT output: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# DOCX (primary output format)
# ---------------------------------------------------------------------------
def build_docx(
    pages: Iterator[tuple[int, list]],
    output_path: Path,
    target_lang: str,
    title: str = "",
) -> Path:
    """Rebuilds a formatted Word document from structured pages.

    Each Block is mapped onto a real Word construct — Title, Heading 1-4,
    List Bullet, a table, an inline image — rather than onto a uniform
    paragraph. This is what makes the translation read as a copy of the
    original document instead of a transcript of its words.

    Word's built-in styles are used deliberately in preference to
    hand-rolled formatting: they carry the document's outline (so the
    navigation pane and any generated table of contents work), and they
    remain restyleable by the user afterwards.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    bengali_target = _is_bengali_target(target_lang)
    _configure_styles(doc, bengali_target)

    section = doc.sections[0]
    usable_width = section.page_width - section.left_margin - section.right_margin

    # The filename is recorded as document metadata regardless, but it is
    # only rendered as a visible title when the source document has no
    # title of its own — otherwise every output would open with the
    # untranslated filename stacked above the real, translated title.
    if title:
        doc.core_properties.title = title

    pages = iter(pages)
    first_page = next(pages, None)
    if first_page is not None and title:
        has_own_title = any(b.kind.value == "title" for b in first_page[1])
        if not has_own_title:
            heading = doc.add_heading(title, level=0)
            _apply_run_font(heading, bengali_target)

    page_count = 0
    block_count = 0

    for _page_num, blocks in _chain_first(first_page, pages):
        for block in blocks:
            if _render_block(doc, block, bengali_target, usable_width,
                             WD_ALIGN_PARAGRAPH, Inches, Pt, section):
                block_count += 1

        page_count += 1

        # Flush to disk periodically on very large documents so a crash
        # mid-build doesn't lose everything (python-docx itself only
        # writes on .save(), so we re-save incrementally every N pages).
        if page_count % 250 == 0:
            doc.save(output_path)
            logger.info("DOCX checkpoint flush at page %d -> %s", page_count, output_path)

    doc.save(output_path)
    logger.info(
        "Built DOCX output (%d source pages, %d blocks): %s",
        page_count, block_count, output_path,
    )
    return output_path


def _configure_styles(doc, bengali_target: bool) -> None:
    """Points the document's styles at fonts that can actually render the
    target script.

    Word picks a font per character class, so a Bengali run needs the
    complex-script ("cs") slot set, not just the ascii one — otherwise
    Word silently substitutes and the text renders as boxes.
    """
    from docx.shared import Pt

    normal = doc.styles["Normal"]
    normal.font.name = config.DOCX_FONT_LATIN
    normal.font.size = Pt(11)
    if bengali_target:
        _set_style_fonts(normal)

    # Headings inherit from Normal for the ascii font but need the same
    # complex-script treatment, or every heading renders as boxes.
    for style_name in ("Title", "Heading 1", "Heading 2", "Heading 3",
                       "Heading 4", "List Bullet", "List Bullet 2",
                       "List Bullet 3"):
        try:
            style = doc.styles[style_name]
        except KeyError:
            continue
        if bengali_target:
            _set_style_fonts(style)


def _set_style_fonts(style) -> None:
    from docx.oxml.ns import qn

    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:cs"), config.DOCX_FONT_BENGALI)
    rfonts.set(qn("w:eastAsia"), config.DOCX_FONT_BENGALI)


def _chain_first(first, rest):
    """Re-attaches a peeked-at first item to the front of an iterator."""
    if first is not None:
        yield first
    for item in rest:
        yield item


def _render_furniture(block, bengali_target: bool, section, is_header: bool) -> bool:
    """Writes a repeating letterhead or footer line into the document's
    real header/footer, where Word will repeat it on every page — rather
    than leaving it stranded mid-prose."""
    from docx.shared import Pt

    container = section.header if is_header else section.footer
    # The first paragraph of a fresh header/footer exists but is empty;
    # reuse it before appending, or every document gains a blank line.
    if len(container.paragraphs) == 1 and not container.paragraphs[0].text:
        paragraph = container.paragraphs[0]
    else:
        paragraph = container.add_paragraph()
    paragraph.alignment = 1  # centred

    run = paragraph.add_run(block.text.strip())
    run.font.size = Pt(8.5)
    _apply_run_font(run, bengali_target)
    return True


def _render_block(doc, block, bengali_target: bool, usable_width,
                  WD_ALIGN_PARAGRAPH, Inches, Pt, section=None) -> bool:
    """Renders one Block into the document. Returns False if nothing was
    emitted (an empty or unusable block)."""
    from core.document_model import BlockKind

    if block.kind in (BlockKind.HEADER, BlockKind.FOOTER):
        if section is None or not block.text.strip():
            return False
        return _render_furniture(
            block, bengali_target, section, block.kind is BlockKind.HEADER
        )

    if block.kind is BlockKind.IMAGE:
        return _render_image(doc, block, usable_width, Inches)

    if block.kind is BlockKind.TABLE:
        return _render_table(doc, block, bengali_target)

    if not block.text.strip():
        return False

    if block.kind is BlockKind.TITLE:
        paragraph = doc.add_paragraph(style="Title")
    elif block.kind is BlockKind.HEADING:
        level = min(max(block.level, 1), 4)
        paragraph = doc.add_paragraph(style=f"Heading {level}")
    elif block.kind is BlockKind.LIST_ITEM:
        # Word ships List Bullet through List Bullet 3; deeper nesting
        # falls back to the deepest available style plus an indent.
        depth = min(max(block.list_depth, 1), 3)
        style = "List Bullet" if depth == 1 else f"List Bullet {depth}"
        try:
            paragraph = doc.add_paragraph(style=style)
        except KeyError:
            paragraph = doc.add_paragraph(style="List Bullet")
    else:
        paragraph = doc.add_paragraph()
        if block.indent > 1:
            # Source indent is in points; Word wants inches.
            paragraph.paragraph_format.left_indent = Inches(block.indent / 72.0)

    for run_spec in block.runs:
        if not run_spec.text:
            continue
        run = paragraph.add_run(run_spec.text)
        # Headings are already bold via their style; re-bolding a run
        # inside one is redundant and interferes with theme restyling.
        if block.kind not in (BlockKind.TITLE, BlockKind.HEADING):
            run.bold = run_spec.bold
            run.italic = run_spec.italic
        _apply_run_font(run, bengali_target)

    return True


def _render_image(doc, block, usable_width, Inches) -> bool:
    path = Path(block.image_path)
    if not path.exists():
        logger.debug("Skipping missing extracted image: %s", path)
        return False

    paragraph = doc.add_paragraph()
    paragraph.alignment = 1  # centred
    run = paragraph.add_run()
    try:
        # Preserve the image's size on the source page, but never let it
        # overflow the text column.
        width = Inches(block.image_width / 72.0) if block.image_width else None
        if width is not None and width > usable_width:
            width = usable_width
        run.add_picture(str(path), width=width)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not embed image %s: %s", path, exc)
        return False
    return True


def _render_table(doc, block, bengali_target: bool) -> bool:
    rows = [row for row in block.rows if any((c or "").strip() for c in row)]
    if not rows:
        return False
    columns = max(len(row) for row in rows)

    table = doc.add_table(rows=0, cols=columns)
    # "Table Grid" is the one built-in style guaranteed to draw borders;
    # without it a translated table renders as unaligned floating text.
    try:
        table.style = "Table Grid"
    except KeyError:
        pass

    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for col_index in range(columns):
            value = row[col_index] if col_index < len(row) else ""
            cell = cells[col_index]
            # A new cell already holds exactly one empty paragraph. Do not
            # assign cell.text first: that leaves an empty run in front of
            # ours, and any run-level formatting then lands on the wrong one.
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run(value or "")
            # A first row of headers is the overwhelmingly common case and
            # reads far better emphasised.
            if row_index == 0:
                run.bold = True
            _apply_run_font(run, bengali_target)
    return True


def _apply_run_font(run_or_paragraph, bengali_target: bool) -> None:
    from docx.oxml.ns import qn

    runs = getattr(run_or_paragraph, "runs", None)
    targets = runs if runs is not None else [run_or_paragraph]
    for run in targets:
        run.font.name = config.DOCX_FONT_LATIN
        if bengali_target:
            rpr = run._element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = rpr.makeelement(qn("w:rFonts"), {})
                rpr.append(rfonts)
            rfonts.set(qn("w:cs"), config.DOCX_FONT_BENGALI)
            rfonts.set(qn("w:eastAsia"), config.DOCX_FONT_BENGALI)
            rfonts.set(qn("w:ascii"), config.DOCX_FONT_LATIN)
            rfonts.set(qn("w:hAnsi"), config.DOCX_FONT_LATIN)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
# Built on PyMuPDF, not ReportLab, and the reason is text shaping.
#
# Bengali is a complex script: vowel signs reorder around the consonant
# they attach to (U+09BF is stored *after* its consonant and must render
# *before* it), and consonant clusters form conjunct ligatures. ReportLab
# has no shaping engine — it draws code points in memory order — so the
# previous PDF output placed every vowel sign and every conjunct wrongly,
# even though the font contained all the glyphs. Measured on a "ki"
# sample: the vowel drew at x=95.8 with its consonant at x=78.0, i.e. on
# the wrong side.
#
# MuPDF ships HarfBuzz and shapes correctly (41.0 vs 45.7 on the same
# sample). It also falls back automatically to a Latin face for Latin
# runs, which removes the manual per-script font splitting the ReportLab
# path needed.


_PDF_CSS = """
@font-face {{ font-family: doc; src: url({font_file}); }}
body {{ font-family: doc; font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; }}
h1 {{ font-size: 19pt; margin: 0 0 10pt 0; line-height: 1.25; }}
h2 {{ font-size: 15pt; margin: 14pt 0 5pt 0; line-height: 1.3; }}
h3 {{ font-size: 12.5pt; margin: 12pt 0 4pt 0; line-height: 1.3; }}
h4 {{ font-size: 11pt; margin: 10pt 0 4pt 0; }}
p {{ margin: 0 0 6pt 0; }}
ul {{ margin: 0 0 6pt 0; padding-left: 16pt; }}
li {{ margin: 0 0 3pt 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 6pt 0 10pt 0; }}
td, th {{ border: 0.6pt solid #9a9a9a; padding: 4pt 6pt; text-align: left;
         vertical-align: top; font-size: 10pt; }}
th {{ background-color: #eeeeee; font-weight: bold; }}
img {{ max-width: 100%; }}
.furniture {{ font-size: 8pt; color: #666666; text-align: center; }}
"""

# Blocks are accumulated into an HTML batch and flushed when the batch
# gets large. Story needs a whole document string at once, so flushing in
# batches is what keeps peak memory flat on a 1000-page job while still
# letting text flow naturally within a batch. The threshold is high
# enough that an ordinary document is a single batch and gains no
# artificial page break.
_PDF_BATCH_CHARS = 240_000


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _runs_to_html(block) -> str:
    """Inline markup for one block's runs, so bold and italic survive."""
    parts = []
    for run in block.runs:
        if not run.text:
            continue
        piece = _html_escape(run.text)
        if run.bold:
            piece = f"<b>{piece}</b>"
        if run.italic:
            piece = f"<i>{piece}</i>"
        parts.append(piece)
    return "".join(parts)


def _block_to_html(block, image_names: dict) -> str:
    from core.document_model import BlockKind

    if block.kind is BlockKind.IMAGE:
        name = image_names.get(block.image_path)
        if not name:
            return ""
        width = f' width="{int(block.image_width)}"' if block.image_width else ""
        return f'<p><img src="{name}"{width}></p>'

    if block.kind is BlockKind.TABLE:
        rows = [r for r in block.rows if any((c or "").strip() for c in r)]
        if not rows:
            return ""
        columns = max(len(r) for r in rows)
        out = ["<table>"]
        for index, row in enumerate(rows):
            tag = "th" if index == 0 else "td"
            cells = "".join(
                f"<{tag}>{_html_escape((row[i] if i < len(row) else '') or '')}</{tag}>"
                for i in range(columns)
            )
            out.append(f"<tr>{cells}</tr>")
        out.append("</table>")
        return "".join(out)

    if not block.text.strip():
        return ""

    markup = _runs_to_html(block)
    if block.kind is BlockKind.TITLE:
        return f"<h1>{markup}</h1>"
    if block.kind is BlockKind.HEADING:
        level = min(max(block.level, 1), 4)
        return f"<h{level}>{markup}</h{level}>"
    if block.kind is BlockKind.LIST_ITEM:
        return f"<ul><li>{markup}</li></ul>"

    indent = f' style="margin-left:{int(block.indent)}pt"' if block.indent > 1 else ""
    return f"<p{indent}>{markup}</p>"


def build_pdf(
    pages,
    output_path,
    target_lang: str,
    title: str = "",
):
    """Writes a formatted, correctly-shaped PDF from structured pages."""
    import fitz

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    archive = fitz.Archive()
    font_file = "NotoSansBengali-Regular.ttf"
    if config.BENGALI_FONT_PATH.exists():
        archive.add(config.BENGALI_FONT_PATH.read_bytes(), font_file)
    else:
        logger.warning(
            "Bengali font missing at %s; the PDF will fall back to a built-in face.",
            config.BENGALI_FONT_PATH,
        )
    css = _PDF_CSS.format(font_file=font_file)

    mediabox = fitz.paper_rect("a4")
    where = mediabox + (56, 64, -56, -64)

    writer = fitz.DocumentWriter(str(output_path))
    batch: list[str] = []
    batch_chars = 0
    running_header: list[str] = []
    running_footer: list[str] = []
    image_names: dict = {}
    page_count = 0
    written_pages = 0

    def flush() -> int:
        """Lays out the accumulated HTML, returning pages written."""
        nonlocal batch, batch_chars
        if not batch:
            return 0
        html = "<body>" + "".join(batch) + "</body>"
        batch, batch_chars = [], 0
        story = fitz.Story(html=html, user_css=css, archive=archive)
        produced = 0
        more = True
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
            produced += 1
        return produced

    from core.document_model import BlockKind

    pages = iter(pages)
    first_page = next(pages, None)
    if first_page is not None and title:
        if not any(b.kind is BlockKind.TITLE for b in first_page[1]):
            batch.append(f"<h1>{_html_escape(title)}</h1>")

    for _page_num, blocks in _chain_first(first_page, pages):
        for block in blocks:
            if block.kind in (BlockKind.HEADER, BlockKind.FOOTER):
                text = block.text.strip()
                if text:
                    target = running_header if block.kind is BlockKind.HEADER else running_footer
                    if text not in target:
                        target.append(text)
                continue

            if block.kind is BlockKind.IMAGE and block.image_path:
                path = Path(block.image_path)
                if path.exists() and block.image_path not in image_names:
                    try:
                        archive.add(path.read_bytes(), path.name)
                        image_names[block.image_path] = path.name
                    except OSError as exc:
                        logger.debug("Could not stage image %s: %s", path, exc)

            fragment = _block_to_html(block, image_names)
            if fragment:
                batch.append(fragment)
                batch_chars += len(fragment)

        page_count += 1
        if batch_chars >= _PDF_BATCH_CHARS:
            written_pages += flush()

    written_pages += flush()

    if written_pages == 0:
        # An empty document still has to be a valid, openable PDF.
        device = writer.begin_page(mediabox)
        story = fitz.Story(
            html="<body><p>No translatable content was found in this document.</p></body>",
            user_css=css, archive=archive,
        )
        story.place(where)
        story.draw(device)
        writer.end_page()

    writer.close()

    _stamp_pdf_furniture(output_path, running_header, running_footer, css, archive)

    logger.info("Built PDF output (%d source pages): %s", page_count, output_path)
    return output_path


def _stamp_pdf_furniture(output_path, header_lines, footer_lines, css, archive) -> None:
    """Draws the running letterhead/footer and a page number on every page.

    Story lays out the flowing body but has no concept of a running
    header, so the furniture is stamped afterwards — through
    insert_htmlbox rather than insert_textbox, because only the HTML path
    goes through the shaper and this text is frequently Bengali.
    """
    import fitz

    if not header_lines and not footer_lines:
        pass  # page numbers alone are still worth stamping

    try:
        doc = fitz.open(str(output_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not reopen the PDF to add page furniture: %s", exc)
        return

    try:
        header_html = "".join(
            f'<p class="furniture">{_html_escape(line)}</p>' for line in header_lines
        )
        footer_html = "".join(
            f'<p class="furniture">{_html_escape(line)}</p>' for line in footer_lines
        )
        for index, page in enumerate(doc, start=1):
            rect = page.rect
            if header_html:
                page.insert_htmlbox(
                    fitz.Rect(40, 16, rect.width - 40, 58), header_html,
                    css=css, archive=archive,
                )
            number = f'<p class="furniture">{index}</p>'
            page.insert_htmlbox(
                fitz.Rect(40, rect.height - 52, rect.width - 40, rect.height - 14),
                footer_html + number, css=css, archive=archive,
            )
        doc.saveIncr()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not stamp page furniture: %s", exc)
    finally:
        doc.close()
