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
    pages: Iterator[tuple[int, str]],
    output_path: Path,
    target_lang: str,
    title: str = "",
) -> Path:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.oxml.ns import qn
    from docx.shared import Pt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Default style: Calibri for Latin runs, Nirmala UI for Bengali runs
    # (Windows ships Nirmala UI; it natively renders Bengali correctly,
    # which Calibri does not).
    normal = doc.styles["Normal"]
    normal.font.name = config.DOCX_FONT_LATIN
    normal.font.size = Pt(11)
    bengali_target = _is_bengali_target(target_lang)
    if bengali_target:
        # Set the east-asian/complex-script font hint so Word picks
        # Nirmala UI for Bengali code points even though the "ascii" font
        # stays Calibri for any residual Latin text (numbers, etc.)
        rpr = normal.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        rfonts.set(qn("w:cs"), config.DOCX_FONT_BENGALI)
        rfonts.set(qn("w:eastAsia"), config.DOCX_FONT_BENGALI)

    if title:
        heading = doc.add_heading(title, level=1)
        _apply_run_font(heading, bengali_target)

    page_count = 0
    for page_num, text in pages:
        if page_count > 0:
            # Real page break, not just a blank paragraph, so the DOCX
            # page boundaries match the source PDF's.
            doc.add_page_break()

        label = doc.add_paragraph()
        label_run = label.add_run(f"Page {page_num + 1}")
        label_run.italic = True
        label_run.font.size = Pt(9)

        if text.strip():
            for line in text.split("\n"):
                if line.strip():
                    p = doc.add_paragraph()
                    run = p.add_run(line)
                    _apply_run_font(run, bengali_target)
        else:
            empty = doc.add_paragraph()
            empty_run = empty.add_run("[No extractable text on this page]")
            empty_run.italic = True

        page_count += 1

        # Flush to disk periodically on very large documents so a crash
        # mid-build doesn't lose everything (python-docx itself only
        # writes on .save(), so we re-save incrementally every N pages).
        if page_count % 250 == 0:
            doc.save(output_path)
            logger.info("DOCX checkpoint flush at page %d -> %s", page_count, output_path)

    doc.save(output_path)
    logger.info("Built DOCX output (%d pages): %s", page_count, output_path)
    return output_path


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
# PDF (secondary output format)
# ---------------------------------------------------------------------------
def build_pdf(
    pages: Iterator[tuple[int, str]],
    output_path: Path,
    target_lang: str,
    title: str = "",
) -> Path:
    """Builds a PDF using ReportLab with a bundled Noto Sans Bengali font
    when the target language is Bengali (ReportLab's built-in fonts are
    Latin-only and would render Bengali as empty boxes otherwise).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_name = "Helvetica"
    bengali_target = _is_bengali_target(target_lang)
    if bengali_target:
        if config.BENGALI_FONT_PATH.exists():
            try:
                pdfmetrics.registerFont(TTFont(config.BENGALI_FONT_NAME, str(config.BENGALI_FONT_PATH)))
                font_name = config.BENGALI_FONT_NAME
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not register Bengali font, falling back to Helvetica: %s", exc)
        else:
            logger.warning(
                "Bengali font not found at %s — Bengali text in the PDF output may not render "
                "correctly. See README for font setup.",
                config.BENGALI_FONT_PATH,
            )

    body_style = ParagraphStyle(
        "Body", fontName=font_name, fontSize=10.5, leading=15, spaceAfter=6,
    )
    page_label_style = ParagraphStyle(
        "PageLabel", fontName="Helvetica-Oblique", fontSize=8, textColor="#666666", spaceAfter=8,
    )
    title_style = ParagraphStyle(
        "Title", fontName=font_name, fontSize=18, leading=22, spaceAfter=18,
    )

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )

    story = []
    if title:
        story.append(Paragraph(_xml_escape(title), title_style))

    page_count = 0
    for page_num, text in pages:
        if page_count > 0:
            story.append(PageBreak())
        story.append(Paragraph(f"Page {page_num + 1}", page_label_style))
        if text.strip():
            for line in text.split("\n"):
                if line.strip():
                    story.append(Paragraph(_xml_escape(line), body_style))
        else:
            story.append(Paragraph("[No extractable text on this page]", page_label_style))
        story.append(Spacer(1, 2))
        page_count += 1

    doc.build(story)
    logger.info("Built PDF output (%d pages): %s", page_count, output_path)
    return output_path


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
