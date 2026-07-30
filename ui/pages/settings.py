"""ui/pages/settings.py — user-editable application settings: default
languages/output, chunking & worker tuning, OCR, output directory, log
export, and theme accent color.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

import config
from services.file_service import open_in_file_explorer
from services.settings_service import settings_service
from ui.theme import page_header


def render() -> None:
    page_header("Settings", "Tune translation performance, OCR, output, and appearance.")

    settings = settings_service.get_all()

    tab_translation, tab_ocr, tab_output, tab_appearance, tab_about = st.tabs(
        ["🌐 Translation", "🔍 OCR", "📁 Output", "🎨 Appearance", "ℹ️ About"]
    )

    with tab_translation:
        _render_translation_settings(settings)

    with tab_ocr:
        _render_ocr_settings(settings)

    with tab_output:
        _render_output_settings(settings)

    with tab_appearance:
        _render_appearance_settings(settings)

    with tab_about:
        _render_about(settings)


def _render_translation_settings(settings: dict) -> None:
    with st.container(border=True):
        st.markdown("##### Default languages")
        lang_codes = list(config.SUPPORTED_LANGUAGES.keys())
        lang_labels = {c: config.SUPPORTED_LANGUAGES[c] for c in lang_codes}

        c1, c2 = st.columns(2)
        with c1:
            default_source = st.selectbox(
                "Default source language", options=lang_codes,
                format_func=lambda c: lang_labels[c],
                index=lang_codes.index(settings.get("default_source_lang", "en")),
            )
        with c2:
            default_target = st.selectbox(
                "Default target language", options=lang_codes,
                format_func=lambda c: lang_labels[c],
                index=lang_codes.index(settings.get("default_target_lang", "bn")),
            )

        st.write("")
        st.markdown("##### Engine status")
        from core.translator_engine import ArgosTranslatorEngine

        engine = ArgosTranslatorEngine.instance()
        for source, target in config.SUPPORTED_LANGUAGE_PAIRS:
            available = engine.is_pair_available(source, target)
            icon = "🟢" if available else "🔴"
            st.write(f"{icon} {lang_labels[source]} → {lang_labels[target]} — {'Installed' if available else 'Not installed'}")

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("🔄 Re-check installed models", use_container_width=True):
                engine.refresh()
                st.rerun()
        with bc2:
            st.caption("Run `python scripts/download_models.py` once (needs internet) to install models.")

    st.write("")
    with st.container(border=True):
        st.markdown("##### Performance")
        chunk_chars = st.slider(
            "Translation chunk size (characters)", min_value=100, max_value=800,
            value=settings.get("chunk_max_chars", config.MAX_CHUNK_CHARS), step=50,
            help="Smaller chunks translate more granularly but with more overhead per call.",
        )
        workers = st.slider(
            "Parallel translation workers", min_value=1, max_value=6,
            value=settings.get("translation_workers", config.TRANSLATION_WORKERS),
            help="On a 4-core CPU like the i3-10100, 2-3 workers is a good balance "
            "between speed and keeping the rest of the system responsive.",
        )
        max_size = st.number_input(
            "Maximum accepted file size (MB)", min_value=10, max_value=5000,
            value=int(settings.get("max_file_size_mb", config.MAX_FILE_SIZE_MB)), step=10,
        )

    if st.button("💾 Save translation settings", type="primary"):
        settings_service.update(
            default_source_lang=default_source,
            default_target_lang=default_target,
            chunk_max_chars=chunk_chars,
            translation_workers=workers,
            max_file_size_mb=max_size,
        )
        st.toast("Translation settings saved.", icon="✅")


def _render_ocr_settings(settings: dict) -> None:
    from core.ocr_engine import ocr_available

    with st.container(border=True):
        st.markdown("##### OCR (PaddleOCR)")
        available = ocr_available()
        if not available:
            st.warning(
                "PaddleOCR is not installed. Install it with "
                "`pip install paddleocr paddlepaddle` to enable OCR for scanned PDFs."
            )

        ocr_default = st.toggle(
            "Enable OCR by default for new translations", value=settings.get("ocr_enabled_default", False),
            disabled=not available,
            help="OCR only runs on pages with little to no extractable text (i.e. scanned "
            "pages), so normal text PDFs are unaffected even when this is on.",
        )
        ocr_dpi = st.slider(
            "OCR render DPI", min_value=100, max_value=300,
            value=int(settings.get("ocr_dpi", config.OCR_RENDER_DPI)), step=25,
            help="Higher DPI improves OCR accuracy on small text but is slower and uses more memory per page.",
        )

    if st.button("💾 Save OCR settings", type="primary"):
        settings_service.update(ocr_enabled_default=ocr_default, ocr_dpi=ocr_dpi)
        st.toast("OCR settings saved.", icon="✅")


def _render_output_settings(settings: dict) -> None:
    with st.container(border=True):
        st.markdown("##### Output")
        output_dir = st.text_input("Output directory", value=settings.get("output_dir", str(config.OUTPUTS_DIR)))
        default_formats = st.multiselect(
            "Default output format(s)", options=config.OUTPUT_FORMATS,
            default=settings.get("default_output_formats", ["docx"]),
            format_func=lambda f: f.upper(),
        )
        keep_checkpoints = st.toggle(
            "Keep checkpoint files after a translation completes",
            value=settings.get("keep_checkpoints_after_completion", False),
            help="Checkpoints are deleted automatically after success to save disk space. "
            "Enable this to keep them for debugging or auditing.",
        )

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("📂 Open output folder", use_container_width=True):
                err = open_in_file_explorer(output_dir)
                if err:
                    st.error(err)
        with bc2:
            if st.button("💾 Save output settings", type="primary", use_container_width=True):
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                settings_service.update(
                    output_dir=output_dir,
                    default_output_formats=default_formats,
                    keep_checkpoints_after_completion=keep_checkpoints,
                )
                st.toast("Output settings saved.", icon="✅")

    st.write("")
    with st.container(border=True):
        st.markdown("##### Logs")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("📂 Open logs folder", use_container_width=True):
                err = open_in_file_explorer(str(config.LOGS_DIR))
                if err:
                    st.error(err)
        with c2:
            if config.APP_LOG_FILE.exists():
                st.download_button(
                    "⬇ Export app.log", data=config.APP_LOG_FILE.read_bytes(),
                    file_name="lekha_app.log", use_container_width=True,
                )


def _render_appearance_settings(settings: dict) -> None:
    with st.container(border=True):
        st.markdown("##### Theme")
        accent_options = {"violet": "Violet", "blue": "Blue", "green": "Green", "amber": "Amber"}
        accent = st.radio(
            "Accent color", options=list(accent_options.keys()),
            format_func=lambda k: accent_options[k],
            index=list(accent_options.keys()).index(settings.get("accent_color", "violet")),
            horizontal=True,
        )
        st.caption("Lekha uses a dark interface; a light theme isn't currently available.")

    if st.button("💾 Save appearance settings", type="primary"):
        settings_service.update(accent_color=accent)
        st.toast("Appearance settings saved. Refreshing...", icon="✅")
        st.rerun()


def _render_about(settings: dict) -> None:
    with st.container(border=True):
        st.markdown(f"##### {config.APP_NAME}")
        st.caption(f"Version {config.APP_VERSION} · 100% offline · Powered by Argos Translate")
        st.write("")
        st.write(
            "Lekha runs entirely on your machine. No file, page, or "
            "translated text is ever sent to a server — translation, OCR, and "
            "document generation all happen locally using Argos Translate, "
            "PyMuPDF, and (optionally) PaddleOCR."
        )

    st.write("")
    with st.container(border=True):
        st.markdown("##### Reset")
        st.caption("Restores all settings on this page to their defaults.")
        if st.button("⚠️ Reset settings to defaults"):
            settings_service.reset_to_defaults()
            st.toast("Settings reset to defaults.", icon="🔄")
            st.rerun()
