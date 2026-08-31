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

    (tab_translation, tab_glossary, tab_hybrid, tab_ocr, tab_output,
     tab_appearance, tab_about) = st.tabs(
        ["Translation", "Glossary", "Hybrid", "OCR", "Output", "Appearance", "About"]
    )

    with tab_translation:
        _render_translation_settings(settings)

    with tab_glossary:
        _render_glossary_settings(settings)

    with tab_hybrid:
        _render_hybrid_settings(settings)

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
            dot = "var(--success)" if available else "var(--text-muted)"
            st.markdown(
                f'<div class="lk-row"><span class="lk-row-label">'
                f'<span style="background:{dot};width:7px;height:7px;border-radius:50%;'
                f'display:inline-block"></span>'
                f'{lang_labels[source]} &rarr; {lang_labels[target]}</span>'
                f'<span class="lk-meta">{"Installed" if available else "Not installed"}</span></div>',
                unsafe_allow_html=True,
            )

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("Re-check installed models", use_container_width=True):
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

    if st.button("Save translation settings", type="primary"):
        settings_service.update(
            default_source_lang=default_source,
            default_target_lang=default_target,
            chunk_max_chars=chunk_chars,
            translation_workers=workers,
            max_file_size_mb=max_size,
        )
        st.toast("Translation settings saved.")


def _render_hybrid_settings(settings: dict) -> None:
    """The hybrid pipeline: an online backend for bulk translation, and a
    small local model to polish the result.

        source PDF -> Google Translate -> raw Bangla -> local 3B model -> book

    Both stages are optional and both default to off. The offline Argos
    path is unaffected by anything on this tab.
    """
    from models.enums import TranslationBackend

    with st.container(border=True):
        st.markdown("##### Default translation backend")
        st.caption(
            "Sets which backend the Translator page starts on. You can still switch "
            "per job."
        )

        backends = [TranslationBackend.ARGOS, TranslationBackend.GOOGLE]
        try:
            current_index = backends.index(
                TranslationBackend(
                    settings.get("translation_backend", config.DEFAULT_TRANSLATION_BACKEND)
                )
            )
        except ValueError:
            current_index = 0

        backend = st.radio(
            "Backend",
            options=backends,
            format_func=lambda b: b.label,
            index=current_index,
            label_visibility="collapsed",
        )

        if backend is TranslationBackend.GOOGLE:
            st.warning(
                "Making the online backend your default means documents are sent to "
                "Google unless you remember to switch back per job.",
            )

        from core.online_translator import GoogleTranslateEngine, deep_translator_installed

        if not deep_translator_installed():
            st.info(
                "The online backend needs `deep-translator`. Install it with "
                "`pip install deep-translator`."
            )
        elif st.button("Test Google Translate connection"):
            with st.spinner("Contacting Google..."):
                ok, detail = GoogleTranslateEngine.instance().self_test()
            if ok:
                st.success(f'Connected. "Hello" → "{detail}"')
            else:
                st.error(f"Connection failed: {detail}")

    st.write("")
    _render_ai_setup(settings)

    st.write("")
    with st.container(border=True):
        st.markdown("##### Local AI polish (Ollama)")
        st.caption(
            "A small instruct model rewrites the machine translation as natural book "
            "prose. It only edits already-translated text, so a 3B model on CPU is "
            "enough — no GPU required."
        )

        from core.llm_refiner import LLMRefiner

        refiner = LLMRefiner.instance()

        refine_default = st.toggle(
            "Enable the polish pass by default for new translations",
            value=bool(settings.get("refine_enabled_default", config.REFINE_ENABLED_DEFAULT)),
        )

        c1, c2 = st.columns([3, 2])
        with c1:
            ollama_url = st.text_input(
                "Ollama base URL",
                value=settings.get("ollama_base_url", config.OLLAMA_BASE_URL),
                help="Where the Ollama server is listening. The default is correct for a "
                "standard local install.",
            )
        with c2:
            refine_timeout = st.number_input(
                "Per-block timeout (seconds)",
                min_value=15,
                max_value=600,
                value=int(settings.get("refine_timeout", config.REFINE_TIMEOUT)),
                step=15,
            )

        # Offer whatever is actually pulled locally, but still allow a free
        # text entry so a model can be named before it is installed.
        refiner.configure(base_url=ollama_url)
        installed_models = refiner.list_models()
        current_model = settings.get("refine_model", config.REFINE_MODEL)

        if installed_models:
            options = list(installed_models)
            if current_model not in options:
                options.insert(0, current_model)
            refine_model = st.selectbox(
                "Refinement model",
                options=options,
                index=options.index(current_model),
                help="Models currently installed in Ollama. A 3B instruct model is the "
                "sweet spot for this task on CPU.",
            )
        else:
            refine_model = st.text_input(
                "Refinement model",
                value=current_model,
                help="Ollama isn't reachable, so the installed model list is unavailable. "
                "Enter the model tag you intend to use.",
            )
            st.caption(
                "Not seeing your models? Start the server with `ollama serve`, then "
                f"`ollama pull {current_model}`."
            )

        refine_block = st.slider(
            "Characters per refinement call",
            min_value=400,
            max_value=3000,
            value=int(settings.get("refine_block_chars", config.REFINE_BLOCK_CHARS)),
            step=100,
            help="Translated chunks are regrouped into blocks of roughly this size "
            "before being polished. Larger blocks mean fewer model calls (faster) and "
            "more context for consistent prose, but a slower response per call.",
        )

        if st.button("Test Ollama connection"):
            refiner.configure(base_url=ollama_url, model=refine_model, timeout=refine_timeout)
            with st.spinner(f"Asking {refine_model} to polish a sample sentence..."):
                ok, detail = refiner.self_test(settings.get("default_target_lang", "bn"))
            if ok:
                st.success("Refinement is working. Sample output:")
                st.info(detail)
            else:
                st.error(detail)

    st.write("")
    with st.container(border=True):
        st.markdown("##### What each combination costs you")
        st.markdown(
            "| Backend | Polish | Speed | Privacy |\n"
            "| --- | --- | --- | --- |\n"
            "| Argos | off | Slow on CPU | Fully offline |\n"
            "| Argos | on | Slowest | Fully offline |\n"
            "| Google | off | Fastest | Text sent to Google |\n"
            "| Google | on | Moderate | Text sent to Google |\n"
        )
        st.caption(
            "The polish pass is the slowest stage in every configuration — it runs a "
            "language model locally on every block of the document."
        )

    if st.button("Save hybrid settings", type="primary"):
        settings_service.update(
            translation_backend=backend.value,
            refine_enabled_default=refine_default,
            ollama_base_url=ollama_url,
            refine_model=refine_model,
            refine_block_chars=refine_block,
            refine_timeout=int(refine_timeout),
        )
        st.toast("Hybrid settings saved.")


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

    if st.button("Save OCR settings", type="primary"):
        settings_service.update(ocr_enabled_default=ocr_default, ocr_dpi=ocr_dpi)
        st.toast("OCR settings saved.")


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
            if st.button("Open output folder", use_container_width=True):
                err = open_in_file_explorer(output_dir)
                if err:
                    st.error(err)
        with bc2:
            if st.button("Save output settings", type="primary", use_container_width=True):
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                settings_service.update(
                    output_dir=output_dir,
                    default_output_formats=default_formats,
                    keep_checkpoints_after_completion=keep_checkpoints,
                )
                st.toast("Output settings saved.")

    st.write("")
    with st.container(border=True):
        st.markdown("##### Logs")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Open logs folder", use_container_width=True):
                err = open_in_file_explorer(str(config.LOGS_DIR))
                if err:
                    st.error(err)
        with c2:
            if config.APP_LOG_FILE.exists():
                st.download_button(
                    "Export app.log", data=config.APP_LOG_FILE.read_bytes(),
                    file_name="lekha_app.log", use_container_width=True,
                )


def _render_appearance_settings(settings: dict) -> None:
    from ui.theme import ACCENTS, THEMES

    with st.container(border=True):
        st.markdown("##### Appearance")

        theme_labels = {"dark": "Dark", "light": "Light"}
        current_theme = settings.get("theme", "dark")
        theme = st.radio(
            "Theme",
            options=list(THEMES),
            format_func=lambda k: theme_labels.get(k, k.title()),
            index=list(THEMES).index(current_theme) if current_theme in THEMES else 0,
            horizontal=True,
        )
        st.caption(
            "Dark is the default because Lekha is usually left running for long "
            "stretches, often overnight. Light suits a bright room. The switch in "
            "the sidebar does the same thing."
        )

        st.write("")
        accent_labels = {"violet": "Violet", "blue": "Blue", "green": "Green", "amber": "Amber"}
        accent_keys = list(ACCENTS.keys())
        current_accent = settings.get("accent_color", "violet")
        accent = st.radio(
            "Accent",
            options=accent_keys,
            format_func=lambda k: accent_labels.get(k, k.title()),
            index=accent_keys.index(current_accent) if current_accent in accent_keys else 0,
            horizontal=True,
        )
        st.caption(
            "The accent marks primary actions, the current page, and live state — "
            "nothing decorative, so it stays meaningful."
        )

        # A live sample of both, so the choice can be judged before saving.
        _render_appearance_preview(theme, accent)

    if st.button("Save appearance settings", type="primary"):
        settings_service.update(accent_color=accent, theme=theme)
        st.toast("Appearance saved.")
        st.rerun()


def _render_appearance_preview(theme: str, accent: str) -> None:
    """Shows the selected theme and accent as they will actually render.

    The controls above only take effect on save, so without this the user
    is picking a colour from its name.
    """
    from ui.theme import build_tokens

    tokens = build_tokens(theme, accent)
    st.markdown(
        f"<style>.lk-preview{{{tokens.split('{', 1)[1].rstrip('}')}}}</style>"
        '<div class="lk-preview" style="margin-top:18px;border-radius:18px;overflow:hidden;'
        'border:1px solid var(--glass-border);background:var(--ground);'
        'background-image:var(--ground-wash);padding:18px">'
        '<div style="background:var(--glass);border:1px solid var(--glass-border);'
        'border-radius:14px;padding:14px 16px;backdrop-filter:blur(20px);'
        'box-shadow:0 1px 0 0 var(--specular-soft) inset">'
        '<div style="color:var(--text);font-weight:640;font-size:.95rem">Preview</div>'
        '<div style="color:var(--text-muted);font-size:.8rem;margin-top:2px">'
        "Body text, muted caption, and the accent below.</div>"
        '<div style="display:flex;gap:8px;margin-top:12px;align-items:center">'
        '<span style="background:var(--accent);color:var(--text-on-accent);'
        'padding:5px 12px;border-radius:10px;font-size:.78rem;font-weight:640">Primary</span>'
        '<span style="background:var(--accent-soft);color:var(--accent-text);'
        'padding:5px 12px;border-radius:10px;font-size:.78rem;font-weight:640">Selected</span>'
        '<span style="background:var(--success-soft);color:var(--success);'
        'padding:5px 12px;border-radius:999px;font-size:.72rem;font-weight:650">Completed</span>'
        "</div></div></div>",
        unsafe_allow_html=True,
    )


def _render_about(settings: dict) -> None:
    from models.enums import TranslationBackend

    active_backend = settings.get("translation_backend", config.DEFAULT_TRANSLATION_BACKEND)
    online_default = active_backend == TranslationBackend.GOOGLE.value

    with st.container(border=True):
        st.markdown(f"##### {config.APP_NAME}")
        st.caption(
            f"Version {config.APP_VERSION} · "
            f"{'Hybrid mode' if online_default else 'Offline by default'} · "
            "Powered by Argos Translate"
        )
        st.write("")
        st.write(
            "Lekha's default pipeline runs entirely on your machine. No file, page, or "
            "translated text is sent anywhere — translation, OCR, and document "
            "generation all happen locally using Argos Translate, PyMuPDF, and "
            "(optionally) PaddleOCR."
        )
        st.write(
            "The optional hybrid pipeline changes that. With the **Google Translate** "
            "backend selected, the text of every page is sent to Google's servers. The "
            "optional **local AI polish** stage does not: it runs against Ollama on this "
            "machine and keeps text local. Both are opt-in and both are off unless you "
            "turn them on."
        )
        if online_default:
            st.warning(
                "Your default backend is currently **Google Translate (online)**, so new "
                "jobs will transmit document text unless you switch back per job.",
            )

    st.write("")
    with st.container(border=True):
        st.markdown("##### Reset")
        st.caption("Restores all settings on this page to their defaults.")
        if st.button("Reset settings to defaults"):
            settings_service.reset_to_defaults()
            st.toast("Settings reset to defaults.")
            st.rerun()


def _render_glossary_settings(settings: dict) -> None:
    """Terms Lekha has been taught: what never to translate, and what to
    translate a fixed way."""
    from services.glossary_service import KEEP, REPLACE, glossary_service
    from ui.components import empty_state, section

    target_lang = settings.get("default_target_lang", "bn")
    lang_name = config.SUPPORTED_LANGUAGES.get(target_lang, target_lang)

    with st.container(border=True):
        st.markdown("##### Add a term")
        st.caption(
            "Machine translation has no idea which words are names. It rendered "
            "**ArchTech BD** as a phonetic guess and **Tel:** as *তেল* — Bengali for "
            "*oil*. A term added here is applied to every job from now on."
        )

        c1, c2 = st.columns([3, 2])
        with c1:
            source_term = st.text_input(
                "Term as it appears in the source",
                key="gloss_source",
                placeholder="ArchTech BD",
            )
        with c2:
            mode_labels = {
                KEEP: "Never translate it",
                REPLACE: f"Always translate it as…",
            }
            mode = st.radio(
                "What should happen",
                options=[KEEP, REPLACE],
                format_func=lambda m: mode_labels[m],
                key="gloss_mode",
            )

        target_term = ""
        if mode == REPLACE:
            target_term = st.text_input(
                f"{lang_name} translation to always use",
                key="gloss_target",
                placeholder="ফোন:",
            )

        if st.button("Add term", type="primary", key="gloss_add"):
            if not source_term.strip():
                st.error("Enter the term as it appears in the source document.")
            elif mode == REPLACE and not target_term.strip():
                st.error(f"Enter the {lang_name} text to use for this term.")
            else:
                glossary_service.add(
                    source_term, mode=mode, target=target_term,
                    target_lang=target_lang if mode == REPLACE else "",
                )
                st.toast(f"Added '{source_term.strip()}'.")
                st.rerun()

    st.write("")
    entries = glossary_service.all_entries()
    section(f"Your terms ({len(entries)})", "file-text")

    with st.container(border=True):
        if not entries:
            empty_state(
                "search",
                "No terms yet",
                "Add a brand name above and it will stop being translated.",
            )
        else:
            for entry in entries:
                source_term = str(entry.get("source", ""))
                entry_mode = entry.get("mode", KEEP)
                c1, c2, c3 = st.columns([3, 3, 1.2])
                with c1:
                    st.markdown(f"**{source_term}**")
                with c2:
                    if entry_mode == REPLACE:
                        st.markdown(f"→ {entry.get('target', '')}")
                    else:
                        st.caption("kept as-is, never translated")
                with c3:
                    if st.button(
                        "Remove",
                        key=f"gloss_del_{source_term}",
                        use_container_width=True,
                    ):
                        glossary_service.remove(source_term)
                        st.rerun()

    st.write("")
    with st.container(border=True):
        st.markdown("##### What is protected automatically")
        st.caption(
            "These need no glossary entry — they are never sent to the translation "
            "engine at all, so they cannot be altered by it."
        )
        for label in (
            "Email addresses", "Web addresses and URLs", "Phone numbers",
            "File paths and file names", "Version numbers",
        ):
            st.markdown(
                f'<div class="lk-row"><span class="lk-row-label">{label}</span>'
                '<span class="lk-meta">protected</span></div>',
                unsafe_allow_html=True,
            )


def _render_ai_setup(settings: dict) -> None:
    """Install, start and stop the local AI without leaving the app.

    Previously this tab could only tell the user to go and install Ollama
    themselves. That is fine at a desk and useless on a machine reached
    over a tunnel, where there may be nobody in front of it.
    """
    from services.ai_runtime import ai_runtime

    base_url = settings.get("ollama_base_url", config.OLLAMA_BASE_URL)
    model = settings.get("refine_model", config.REFINE_MODEL)
    status = ai_runtime.status(base_url)

    with st.container(border=True):
        st.markdown("##### Local AI runtime")

        def row(label: str, value: str, ok: bool) -> None:
            colour = "var(--success)" if ok else "var(--text-muted)"
            st.markdown(
                f'<div class="lk-row"><span class="lk-row-label">'
                f'<span style="background:{colour};width:7px;height:7px;border-radius:50%;'
                f'display:inline-block"></span>{label}</span>'
                f'<span class="lk-meta">{value}</span></div>',
                unsafe_allow_html=True,
            )

        if status.installed:
            where = "installed by Lekha" if status.managed else "found on this system"
            row("Runtime", where, True)
        else:
            row("Runtime", "not installed", False)
        row("Server", "running" if status.server_running else "stopped", status.server_running)
        row(f"Model {model}", "ready" if status.has_model(model) else "not pulled",
            status.has_model(model))

        st.write("")

        if not status.installed:
            try:
                asset, size = ai_runtime.download_size()
                size_note = f"about {size / 1048576:.0f} MB"
            except Exception:  # noqa: BLE001
                asset, size_note = "the Ollama runtime", "a large download"
            st.caption(
                f"Lekha can install this for you: **{size_note}** for the runtime "
                f"(`{asset}`), plus roughly 2 GB for `{model}`. It is downloaded from "
                "Ollama's official GitHub release over HTTPS and its SHA-256 is checked "
                "against the release's own checksum file before anything runs."
            )
            if st.button("Download and install the local AI", type="primary",
                         key="ai_install"):
                _run_ai_install(ai_runtime, base_url, model)
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                if not status.server_running:
                    if st.button("Start server", type="primary", key="ai_start",
                                 use_container_width=True):
                        with st.spinner("Starting the local AI server..."):
                            ok = ai_runtime.start_server(base_url)
                        st.toast("Server started." if ok else "The server did not start.")
                        st.rerun()
                elif ai_runtime.owns_server():
                    if st.button("Stop server", key="ai_stop", use_container_width=True):
                        ai_runtime.stop_server()
                        st.toast("Server stopped.")
                        st.rerun()
                else:
                    st.caption("Started outside Lekha")
            with c2:
                if status.server_running and not status.has_model(model):
                    if st.button(f"Pull {model}", key="ai_pull", use_container_width=True):
                        _run_model_pull(ai_runtime, base_url, model)
            with c3:
                if status.managed:
                    if st.button("Remove runtime", key="ai_remove", use_container_width=True):
                        ai_runtime.uninstall()
                        st.toast("Managed runtime removed.")
                        st.rerun()

        st.write("")
        auto_start = st.toggle(
            "Start the AI server automatically when a job needs it",
            value=bool(settings.get("auto_start_ai", True)),
            key="ai_auto_start",
            help="Only starts a runtime that is already installed. Nothing is ever "
            "downloaded in the middle of a translation.",
        )
        if auto_start != bool(settings.get("auto_start_ai", True)):
            settings_service.update(auto_start_ai=auto_start)


def _run_ai_install(ai_runtime, base_url: str, model: str) -> None:
    """Runs the install with live progress, then starts and pulls."""
    bar = st.progress(0.0)
    note = st.empty()

    def report(message: str, fraction: float) -> None:
        note.caption(message)
        if 0.0 <= fraction <= 1.0:
            bar.progress(fraction)

    try:
        ai_runtime.install(report)
    except Exception as exc:  # noqa: BLE001
        bar.empty()
        st.error(f"Install failed: {exc}")
        return

    note.caption("Starting the server...")
    if not ai_runtime.start_server(base_url):
        bar.empty()
        st.warning("Installed, but the server did not start. Try Start server below.")
        return

    _run_model_pull(ai_runtime, base_url, model, bar=bar, note=note)


def _run_model_pull(ai_runtime, base_url: str, model: str, bar=None, note=None) -> None:
    bar = bar if bar is not None else st.progress(0.0)
    note = note if note is not None else st.empty()

    def report(message: str, fraction: float) -> None:
        note.caption(message)
        if 0.0 <= fraction <= 1.0:
            bar.progress(fraction)

    ok = ai_runtime.pull_model(model, base_url, report)
    bar.empty()
    note.empty()
    if ok:
        st.success(f"{model} is ready. The polish pass will work from now on.")
    else:
        st.error(f"Could not pull {model}. See logs/app.log for details.")
    st.rerun()
