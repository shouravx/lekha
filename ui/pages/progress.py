"""ui/pages/progress.py — live status of the running job: file, page,
percentage, ETA, and a streaming log console.

Refresh strategy
----------------
This page used to end with `time.sleep(1.5); st.rerun()`, which reruns the
*whole script* every 1.5 seconds: sidebar, history read, checkpoint scan,
engine probe and all. The only things that actually change are the
progress numbers and the log tail.

The live region is a fragment instead, so the auto-refresh re-executes
that fragment alone and the rest of the page is untouched. The polling
also stops on its own once the job reaches a terminal state, rather than
spinning for as long as the tab is open.
"""

from __future__ import annotations

import streamlit as st

from services.file_service import open_in_file_explorer
from services.queue_service import job_manager
from ui.components import empty_state, language_label, log_console, section, status_badge_html
from ui.theme import page_header

# Fast enough to feel live, slow enough that a long job does not spend its
# CPU budget answering the UI.
_REFRESH_SECONDS = 1.5


def _format_duration(seconds) -> str:
    if seconds is None:
        return "Estimating…"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def status_is_active(status: str) -> bool:
    return status in ("RUNNING", "QUEUED", "PAUSED")


def render() -> None:
    page_header("Progress", "Live status of the current translation.")

    target_id = _resolve_target_job()
    if not target_id:
        with st.container(border=True):
            empty_state(
                "activity",
                "No active translations",
                "Start one from Translator and its progress appears here.",
            )
        return

    progress = job_manager.get_progress(target_id)
    active = status_is_active(progress.get("status", ""))

    # `run_every` is set only while there is something to watch, so a
    # finished job stops costing reruns.
    live = st.fragment(run_every=_REFRESH_SECONDS if active else None)(_live_panel)
    live(target_id)


def _resolve_target_job() -> str | None:
    target_id = job_manager.get_active_job_id() or st.session_state.get("last_job_id")
    if target_id and not job_manager.get_job(target_id):
        target_id = None
    if not target_id:
        all_ids = job_manager.get_all_job_ids()
        target_id = all_ids[-1] if all_ids else None
    return target_id


def _live_panel(target_id: str) -> None:
    """Everything that changes while a job runs. Re-executed on its own."""
    job = job_manager.get_job(target_id)
    if job is None:
        st.info("This job is no longer available.")
        return

    progress = job_manager.get_progress(target_id)
    logs = job_manager.get_logs(target_id)
    status = progress.get("status", job.status.value)

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"##### {progress.get('current_file', job.original_filename)}")
            st.caption(
                f"{language_label(job.source_lang)} → {language_label(job.target_lang)}"
            )
        with c2:
            st.markdown(status_badge_html(status), unsafe_allow_html=True)

        percent = float(progress.get("percent", 0.0))
        st.progress(min(1.0, max(0.0, percent / 100.0)))

        total_pages = progress.get("total_pages", job.total_pages) or "?"
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Page", f"{progress.get('current_page', 0)} / {total_pages}")
        m2.metric("Progress", f"{percent:.1f}%")
        m3.metric("Time left", _format_duration(progress.get("eta_seconds")))
        m4.metric("Status", str(status).title())

        if status == "RUNNING":
            bc1, _ = st.columns([1, 5])
            with bc1:
                if st.button("Cancel", use_container_width=True, key=f"cancel_{target_id}"):
                    job_manager.cancel_job(target_id)
                    st.toast("Cancellation requested.")
        elif status == "COMPLETED" and job.output_paths:
            _render_outputs(job, target_id)
        elif status == "FAILED":
            st.error(job.error_message or "Translation failed for an unknown reason.")

    queued_ids = job_manager.get_queued_job_ids()
    if queued_ids:
        section("Up next", "clock")
        for qid in queued_ids:
            qjob = job_manager.get_job(qid)
            if not qjob:
                continue
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.write(f"**{qjob.original_filename}**")
                    st.caption(
                        f"{language_label(qjob.source_lang)} → {language_label(qjob.target_lang)}"
                    )
                with c2:
                    st.markdown(status_badge_html("QUEUED"), unsafe_allow_html=True)

    section("Logs", "file-text")
    with st.container(border=True):
        log_console(logs, height=320)
        if logs:
            st.download_button(
                "Export logs",
                data="\n".join(logs),
                file_name=f"{target_id}_log.txt",
                mime="text/plain",
                key=f"export_log_{target_id}",
            )


def _render_outputs(job, target_id: str) -> None:
    st.write("")
    cols = st.columns(len(job.output_paths) + 1)
    for i, (fmt, path) in enumerate(job.output_paths.items()):
        with cols[i]:
            try:
                # Only read once the job is finished — this used to run on
                # every 1.5s refresh, re-reading the whole document from
                # disk each time.
                with open(path, "rb") as handle:
                    st.download_button(
                        f"Download {fmt.upper()}",
                        data=handle.read(),
                        file_name=path.replace("\\", "/").split("/")[-1],
                        use_container_width=True,
                        key=f"dl_{fmt}_{target_id}",
                    )
            except OSError:
                st.caption(f"{fmt.upper()} file not found")
    with cols[-1]:
        if st.button("Open folder", use_container_width=True, key=f"openf_{target_id}"):
            from pathlib import Path

            first_path = next(iter(job.output_paths.values()))
            err = open_in_file_explorer(str(Path(first_path).parent))
            if err:
                st.error(err)
