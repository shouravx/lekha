"""ui/pages/translator.py — the core workflow: drag-and-drop PDFs,
choose languages/output formats/OCR, and start a (batch) translation.
"""

from __future__ import annotations

import streamlit as st

import config
from models.job import TranslationJob
from services.file_service import (
    InvalidPDFError,
    get_file_size_mb,
    human_readable_size,
    save_uploaded_file,
    validate_pdf_file,
)
from services.queue_service import job_manager
from services.settings_service import settings_service
from ui.theme import page_header


def render() -> None:
    page_header("Translator", "Translate one or many PDFs, fully offline, with Argos Translate.")

    settings = settings_service.get_all()

    with st.container(border=True):
        st.markdown("##### 1 · Upload PDF(s)")
        uploaded_files = st.file_uploader(
            "Drag and drop PDF files here, or click to browse",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        if uploaded_files:
            for f in uploaded_files:
                st.markdown(f"📄 **{f.name}** &nbsp;·&nbsp; {human_readable_size(f.size)}", unsafe_allow_html=True)

    st.write("")
    col_lang, col_opts = st.columns([1, 1], gap="large")

    with col_lang:
        with st.container(border=True):
            st.markdown("##### 2 · Languages")
            lang_codes = list(config.SUPPORTED_LANGUAGES.keys())
            lang_labels = {c: config.SUPPORTED_LANGUAGES[c] for c in lang_codes}

            if "src_lang" not in st.session_state:
                st.session_state["src_lang"] = settings.get("default_source_lang", "en")
            if "tgt_lang" not in st.session_state:
                st.session_state["tgt_lang"] = settings.get("default_target_lang", "bn")

            c1, c2, c3 = st.columns([5, 1, 5])
            with c1:
                source_lang = st.selectbox(
                    "From",
                    options=lang_codes,
                    format_func=lambda c: lang_labels[c],
                    index=lang_codes.index(st.session_state["src_lang"]),
                )
            with c2:
                st.write("")
                st.write("")
                if st.button("⇄", help="Swap languages"):
                    st.session_state["src_lang"], st.session_state["tgt_lang"] = (
                        st.session_state["tgt_lang"],
                        st.session_state["src_lang"],
                    )
                    st.rerun()
            with c3:
                target_lang = st.selectbox(
                    "To",
                    options=lang_codes,
                    format_func=lambda c: lang_labels[c],
                    index=lang_codes.index(st.session_state["tgt_lang"]),
                )

            st.session_state["src_lang"] = source_lang
            st.session_state["tgt_lang"] = target_lang

            if source_lang == target_lang:
                st.warning("Source and target languages are the same.")

            from core.translator_engine import ArgosTranslatorEngine

            engine = ArgosTranslatorEngine.instance()
            if not engine.is_pair_available(source_lang, target_lang):
                st.error(
                    f"The {lang_labels[source_lang]} → {lang_labels[target_lang]} model isn't "
                    "installed yet. Run `python scripts/download_models.py` once (requires "
                    "internet), then restart the app."
                )

    with col_opts:
        with st.container(border=True):
            st.markdown("##### 3 · Output & OCR")
            output_formats = st.multiselect(
                "Output format(s)",
                options=config.OUTPUT_FORMATS,
                default=settings.get("default_output_formats", ["docx"]),
                format_func=lambda f: f.upper(),
            )
            ocr_enabled = st.toggle(
                "Enable OCR for scanned pages",
                value=settings.get("ocr_enabled_default", False),
                help="Uses PaddleOCR to extract text from scanned/image-only pages. "
                "Only runs on pages with no extractable text, so it won't slow down "
                "normal text PDFs.",
            )
            with st.expander("Advanced settings"):
                chunk_chars = st.slider(
                    "Translation chunk size (characters)",
                    min_value=100,
                    max_value=800,
                    value=settings.get("chunk_max_chars", config.MAX_CHUNK_CHARS),
                    step=50,
                )
                workers = st.slider(
                    "Parallel translation workers",
                    min_value=1,
                    max_value=6,
                    value=settings.get("translation_workers", config.TRANSLATION_WORKERS),
                    help="Higher values use more CPU cores. 2-3 is a good balance on a "
                    "4-core CPU like the i3-10100.",
                )

    st.write("")
    start_disabled = not uploaded_files or not output_formats or source_lang == target_lang
    if st.button("🚀  Start Translation", type="primary", use_container_width=True, disabled=start_disabled):
        settings_service.update(chunk_max_chars=chunk_chars, translation_workers=workers)
        _submit_jobs(uploaded_files, source_lang, target_lang, output_formats, ocr_enabled)
        st.session_state["active_page"] = "progress"
        st.rerun()

    if not uploaded_files:
        st.caption("Upload at least one PDF to continue.")
    elif not output_formats:
        st.caption("Choose at least one output format to continue.")


def _submit_jobs(uploaded_files, source_lang, target_lang, output_formats, ocr_enabled) -> None:
    settings = settings_service.get_all()
    submitted = 0
    for f in uploaded_files:
        try:
            saved_path = save_uploaded_file(f.getvalue(), f.name)
            validate_pdf_file(saved_path, max_size_mb=settings.get("max_file_size_mb"))
        except InvalidPDFError as exc:
            st.error(f"Skipped '{f.name}': {exc}")
            continue

        job = TranslationJob(
            input_path=str(saved_path),
            source_lang=source_lang,
            target_lang=target_lang,
            output_formats=output_formats,
            ocr_enabled=ocr_enabled,
            output_dir=settings.get("output_dir", str(config.OUTPUTS_DIR)),
            original_filename=f.name,
            file_size_mb=get_file_size_mb(saved_path),
        )
        job_manager.submit_job(job)
        st.session_state["last_job_id"] = job.job_id
        submitted += 1

    if submitted:
        st.toast(f"Queued {submitted} file(s) for translation.", icon="🚀")
