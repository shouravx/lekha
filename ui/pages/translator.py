"""ui/pages/translator.py — the core workflow: drag-and-drop PDFs,
choose languages/output formats/OCR, and start a (batch) translation.
"""

from __future__ import annotations

import streamlit as st

import config
from models.enums import TranslationBackend
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
from ui.icons import icon
from ui.theme import page_header


def render() -> None:
    page_header("Translator", "Translate one or many PDFs — offline by default.")

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
                st.markdown(
                    f'<div class="lk-row"><span class="lk-row-label">{icon("file-text", 16)}'
                    f'{f.name}</span>'
                    f'<span class="lk-meta">{human_readable_size(f.size)}</span></div>',
                    unsafe_allow_html=True,
                )

    st.write("")
    backend, refine_enabled = _render_engine_picker(settings)

    st.write("")
    col_lang, col_opts = st.columns([1, 1], gap="large")

    with col_lang:
        with st.container(border=True):
            st.markdown("##### 3 · Languages")
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
                if st.button("Swap", help="Swap the source and target languages"):
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

            # Only the offline backend can be missing a language pair —
            # Google covers every pair Lekha exposes.
            if backend is TranslationBackend.ARGOS:
                from core.translator_engine import ArgosTranslatorEngine

                engine = ArgosTranslatorEngine.instance()
                if not engine.is_pair_available(source_lang, target_lang):
                    st.error(
                        f"The {lang_labels[source_lang]} → {lang_labels[target_lang]} model isn't "
                        "installed yet. Run `python scripts/download_models.py` once (requires "
                        "internet), then restart the app — or switch to the online backend above."
                    )

    with col_opts:
        with st.container(border=True):
            st.markdown("##### 4 · Output & OCR")
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
                # Each backend has its own tuning: Argos is limited by CPU
                # and short-input accuracy, the online backend by HTTP
                # round-trips and rate limits. Editing one must not silently
                # change the other, so they are stored separately.
                if backend is TranslationBackend.GOOGLE:
                    chunk_chars = st.slider(
                        "Characters per request",
                        min_value=500,
                        max_value=4500,
                        value=int(
                            settings.get("online_chunk_max_chars", config.ONLINE_MAX_CHUNK_CHARS)
                        ),
                        step=250,
                        help="Larger requests mean far fewer round-trips over a long book, "
                        "which is the main defence against being rate-limited. Google's "
                        "endpoint accepts roughly 5000 characters.",
                    )
                    workers = st.slider(
                        "Parallel requests",
                        min_value=1,
                        max_value=4,
                        value=int(
                            settings.get(
                                "online_translation_workers", config.ONLINE_TRANSLATION_WORKERS
                            )
                        ),
                        help="Concurrent HTTP requests. Keep this low — the free endpoint "
                        "throttles aggressive callers, and translation is not the "
                        "bottleneck on this path anyway.",
                    )
                else:
                    chunk_chars = st.slider(
                        "Translation chunk size (characters)",
                        min_value=100,
                        max_value=800,
                        value=int(settings.get("chunk_max_chars", config.MAX_CHUNK_CHARS)),
                        step=50,
                    )
                    workers = st.slider(
                        "Parallel translation workers",
                        min_value=1,
                        max_value=6,
                        value=int(
                            settings.get("translation_workers", config.TRANSLATION_WORKERS)
                        ),
                        help="Higher values use more CPU cores. 2-3 is a good balance on a "
                        "4-core CPU like the i3-10100.",
                    )

    st.write("")
    start_disabled = not uploaded_files or not output_formats or source_lang == target_lang
    if st.button("Start translation", type="primary", use_container_width=True, disabled=start_disabled):
        if backend is TranslationBackend.GOOGLE:
            settings_service.update(
                online_chunk_max_chars=chunk_chars, online_translation_workers=workers
            )
        else:
            settings_service.update(chunk_max_chars=chunk_chars, translation_workers=workers)
        _submit_jobs(
            uploaded_files,
            source_lang,
            target_lang,
            output_formats,
            ocr_enabled,
            backend,
            refine_enabled,
        )
        st.session_state["active_page"] = "progress"
        st.rerun()

    if not uploaded_files:
        st.caption("Upload at least one PDF to continue.")
    elif not output_formats:
        st.caption("Choose at least one output format to continue.")


def _render_engine_picker(settings: dict) -> tuple[TranslationBackend, bool]:
    """Section 2 — choose the translation backend and the optional LLM
    polish pass. Returns (backend, refine_enabled).

    The online backend is a genuine change in what the app does with the
    user's document, so the trade-off is stated plainly here rather than
    buried in Settings.
    """
    with st.container(border=True):
        st.markdown("##### 2 · Translation engine")

        backends = [TranslationBackend.ARGOS, TranslationBackend.GOOGLE]
        default_backend = settings.get(
            "translation_backend", config.DEFAULT_TRANSLATION_BACKEND
        )
        try:
            default_index = backends.index(TranslationBackend(default_backend))
        except ValueError:
            default_index = 0

        backend = st.radio(
            "Backend",
            options=backends,
            format_func=lambda b: b.label,
            index=default_index,
            horizontal=True,
            label_visibility="collapsed",
        )

        if backend is TranslationBackend.GOOGLE:
            st.warning(
                "**Your document leaves this machine.** Every page of text is sent to "
                "Google's servers to be translated. Don't use this backend for "
                "confidential, personal, or unpublished material — use Argos, which "
                "never transmits anything.",
            )
            from core.online_translator import deep_translator_installed

            if not deep_translator_installed():
                st.error(
                    "The online backend needs the `deep-translator` package: "
                    "`pip install deep-translator`"
                )
            else:
                st.caption(
                    "Much faster than Argos and uses almost no CPU — the work happens on "
                    "Google's side. Requires an internet connection."
                )
        else:
            st.caption(
                "Runs entirely on your machine. Nothing is uploaded. Slower on low-end "
                "hardware, and needs the language model installed locally."
            )

        st.write("")
        st.markdown("**Polish with a local AI model** &nbsp;·&nbsp; optional", unsafe_allow_html=True)

        refine_model = settings.get("refine_model", config.REFINE_MODEL)
        refine_enabled = st.toggle(
            f"Rewrite the translation as natural book prose using `{refine_model}`",
            value=bool(settings.get("refine_enabled_default", config.REFINE_ENABLED_DEFAULT)),
            help="A small local model (3B class, served by Ollama) edits the translated "
            "text so it reads like a book rather than machine output. It only edits "
            "already-translated text, so no GPU is needed.",
        )

        if refine_enabled:
            from core.llm_refiner import LLMRefiner

            refiner = LLMRefiner.instance()
            refiner.configure(
                base_url=settings.get("ollama_base_url", config.OLLAMA_BASE_URL),
                model=refine_model,
            )
            if not refiner.is_available():
                st.warning(
                    f"Ollama isn't reachable at `{refiner.base_url}`. The job will still "
                    "run, but the polish pass will be skipped. Start it with "
                    "`ollama serve`, then pull the model with "
                    f"`ollama pull {refine_model}`.",
                )
            else:
                installed = refiner.list_models()
                if installed and refine_model not in installed:
                    st.warning(
                        f"Ollama is running but `{refine_model}` isn't installed. "
                        f"Pull it with `ollama pull {refine_model}`, or pick an "
                        "installed model in Settings → Translation.",
                    )
                else:
                    st.caption(
                        "This is the slowest stage by a wide margin — expect a long "
                        "document to take considerably longer than translation alone."
                    )

    return backend, refine_enabled


def _submit_jobs(
    uploaded_files,
    source_lang,
    target_lang,
    output_formats,
    ocr_enabled,
    backend: TranslationBackend = TranslationBackend.ARGOS,
    refine_enabled: bool = False,
) -> None:
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
            translation_backend=backend.value,
            refine_enabled=refine_enabled,
        )
        job_manager.submit_job(job)
        st.session_state["last_job_id"] = job.job_id
        submitted += 1

    if submitted:
        st.toast(f"Queued {submitted} file(s) for translation.")
