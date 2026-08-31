"""ui/pages/history.py — browse, search, re-translate, and manage past
translation jobs.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

import config
from models.job import TranslationJob
from services.file_service import (
    InvalidPDFError,
    human_readable_size,
    open_in_file_explorer,
    validate_pdf_file,
)
from services.history_service import history_service
from services.queue_service import job_manager
from ui.components import empty_state, language_label, status_badge_html
from ui.theme import page_header


def render() -> None:
    page_header("History", "Every translation you've run, searchable and re-runnable.")

    c1, c2 = st.columns([3, 1])
    with c1:
        query = st.text_input(
            "Search by filename", placeholder="Search history...", label_visibility="collapsed"
        )
    with c2:
        if st.button("Clear all history", use_container_width=True):
            st.session_state["confirm_clear_history"] = True

    if st.session_state.get("confirm_clear_history"):
        with st.container(border=True):
            st.warning("This will permanently delete all translation history records (output files are kept).")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("Yes, clear history", type="primary", use_container_width=True):
                    history_service.clear_all()
                    st.session_state["confirm_clear_history"] = False
                    st.rerun()
            with cc2:
                if st.button("Cancel", use_container_width=True):
                    st.session_state["confirm_clear_history"] = False
                    st.rerun()

    entries = history_service.search(query) if query else history_service.get_all()

    if not entries:
        with st.container(border=True):
            if query:
                empty_state("search", "No matches", f"Nothing in your history matches '{query}'.")
            else:
                empty_state("clock", "No history yet",
                            "Finished translations are listed here, newest first.")
        return

    st.caption(f"{len(entries)} translation{'s' if len(entries) != 1 else ''}")

    for e in entries:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 2])
            with c1:
                st.markdown(f"**{e.filename}**")
                st.caption(
                    f"{language_label(e.source_lang)} → {language_label(e.target_lang)} · "
                    f"{e.total_pages} pages · {human_readable_size(e.file_size_mb * 1024 * 1024)}"
                )
            with c2:
                st.markdown(status_badge_html(e.status), unsafe_allow_html=True)
                st.caption(f"Formats: {', '.join(f.upper() for f in e.output_formats)}")
                st.caption(e.completed_at or e.created_at)
            with c3:
                b1, b2, b3 = st.columns(3)
                with b1:
                    out_paths = list(e.output_paths.values())
                    if out_paths and st.button("Open", key=f"hist_open_{e.job_id}", help="Open the output folder", use_container_width=True):
                        err = open_in_file_explorer(str(Path(out_paths[0]).parent))
                        if err:
                            st.error(err)
                with b2:
                    if st.button("Again", key=f"hist_retranslate_{e.job_id}", help="Translate this file again", use_container_width=True):
                        _retranslate(e)
                with b3:
                    if st.button("Remove", key=f"hist_delete_{e.job_id}", help="Remove from history", use_container_width=True):
                        history_service.delete(e.job_id)
                        st.rerun()


def _retranslate(entry) -> None:
    """Re-queues a past job. Requires the original uploaded file to still
    exist on disk (uploads/ isn't auto-cleaned), since History only keeps
    metadata + a reference to the source PDF, not its bytes.
    """
    input_path = entry.input_path

    if not input_path or not Path(input_path).exists():
        st.error(
            "Can't re-translate: the original uploaded PDF is no longer available. "
            "Please upload it again from the Translator tab."
        )
        return

    try:
        validate_pdf_file(input_path)
    except InvalidPDFError as exc:
        st.error(f"Can't re-translate: {exc}")
        return

    job = TranslationJob(
        input_path=input_path,
        source_lang=entry.source_lang,
        target_lang=entry.target_lang,
        output_formats=entry.output_formats,
        ocr_enabled=entry.ocr_enabled,
        output_dir=str(config.OUTPUTS_DIR),
        original_filename=entry.filename,
        file_size_mb=entry.file_size_mb,
    )
    job_manager.submit_job(job)
    st.session_state["last_job_id"] = job.job_id
    st.session_state["active_page"] = "progress"
    st.toast(f"Queued '{entry.filename}' again.")
    st.rerun()
