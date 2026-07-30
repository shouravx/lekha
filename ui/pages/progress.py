"""ui/pages/progress.py — live view of the active translation job: current
file/page, percentage, ETA, and a streaming log console. Also surfaces
queued jobs waiting behind the active one (batch translation).
"""

from __future__ import annotations

import time

import streamlit as st

import config
from services.file_service import open_in_file_explorer
from services.queue_service import job_manager
from ui.components import empty_state, language_label, log_console, status_badge_html
from ui.theme import page_header


def _format_duration(seconds) -> str:
    if seconds is None:
        return "calculating..."
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def render() -> None:
    page_header("Progress", "Live status of the current translation, with ETA and logs.")

    active_id = job_manager.get_active_job_id()
    queued_ids = job_manager.get_queued_job_ids()

    target_id = active_id or st.session_state.get("last_job_id")
    if target_id and not job_manager.get_job(target_id):
        target_id = None
    if not target_id:
        all_ids = job_manager.get_all_job_ids()
        target_id = all_ids[-1] if all_ids else None

    if not target_id:
        with st.container(border=True):
            empty_state("⚡", "No active translations", "Start one from the Translator tab.")
        return

    job = job_manager.get_job(target_id)
    progress = job_manager.get_progress(target_id)
    logs = job_manager.get_logs(target_id)

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"##### {progress.get('current_file', job.original_filename)}")
            st.caption(f"{language_label(job.source_lang)} → {language_label(job.target_lang)}")
        with c2:
            st.markdown(status_badge_html(progress.get("status", job.status.value)), unsafe_allow_html=True)

        percent = float(progress.get("percent", 0.0))
        st.progress(min(1.0, percent / 100.0))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Page", f"{progress.get('current_page', 0)} / {progress.get('total_pages', job.total_pages) or '?'}")
        m2.metric("Progress", f"{percent:.1f}%")
        m3.metric("ETA", _format_duration(progress.get("eta_seconds")))
        m4.metric("Status", progress.get("status", job.status.value).title())

        status = progress.get("status", job.status.value)
        if status == "RUNNING":
            bc1, bc2 = st.columns([1, 5])
            with bc1:
                if st.button("⏹ Cancel", use_container_width=True):
                    job_manager.cancel_job(target_id)
                    st.toast("Cancellation requested.", icon="⏹")
        elif status == "COMPLETED":
            if job.output_paths:
                st.write("")
                cols = st.columns(len(job.output_paths) + 1)
                for i, (fmt, path) in enumerate(job.output_paths.items()):
                    with cols[i]:
                        try:
                            with open(path, "rb") as f:
                                st.download_button(
                                    f"⬇ {fmt.upper()}", data=f.read(),
                                    file_name=path.split("/")[-1].split("\\")[-1],
                                    use_container_width=True, key=f"dl_{fmt}_{target_id}",
                                )
                        except OSError:
                            st.caption(f"{fmt.upper()} file not found")
                with cols[-1]:
                    if st.button("📂 Open folder", use_container_width=True, key=f"openf_{target_id}"):
                        first_path = next(iter(job.output_paths.values()))
                        err = open_in_file_explorer(str(__import__("pathlib").Path(first_path).parent))
                        if err:
                            st.error(err)
        elif status == "FAILED":
            st.error(job.error_message or "Translation failed for an unknown reason.")

    if queued_ids:
        st.write("")
        st.markdown("#### Up next in queue")
        for qid in queued_ids:
            qjob = job_manager.get_job(qid)
            if not qjob:
                continue
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.write(f"**{qjob.original_filename}**")
                    st.caption(f"{language_label(qjob.source_lang)} → {language_label(qjob.target_lang)}")
                with c2:
                    st.markdown(status_badge_html("QUEUED"), unsafe_allow_html=True)

    st.write("")
    st.markdown("#### Logs")
    with st.container(border=True):
        log_console(logs, height=320)
        if logs:
            log_text = "\n".join(logs)
            st.download_button(
                "⬇ Export logs", data=log_text, file_name=f"{target_id}_log.txt",
                mime="text/plain", key=f"export_log_{target_id}",
            )

    # Auto-refresh while a job is actively running so progress/logs update
    # without the user needing to manually reload the page.
    if status_is_active(progress.get("status", job.status.value)):
        time.sleep(1.5)
        st.rerun()


def status_is_active(status: str) -> bool:
    return status in ("RUNNING", "QUEUED", "PAUSED")
