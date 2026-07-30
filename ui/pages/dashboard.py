"""ui/pages/dashboard.py — landing page: stats, crash recovery banner,
recent translations, and quick actions.
"""

from __future__ import annotations

import streamlit as st

import config
from services.checkpoint_service import checkpoint_manager
from services.file_service import human_readable_size, open_in_file_explorer
from services.history_service import history_service
from services.queue_service import job_manager
from ui.components import empty_state, language_label, stat_tile, status_badge_html
from ui.theme import page_header


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def render() -> None:
    page_header("Dashboard", "A quick look at your translation activity.")

    _render_recovery_banner()

    stats = history_service.stats()
    active_job_id = job_manager.get_active_job_id()
    queued_count = len(job_manager.get_queued_job_ids())

    cols = st.columns(4)
    with cols[0]:
        stat_tile("📄", str(stats["total_pages_translated"]), "Pages translated", "violet")
    with cols[1]:
        stat_tile("✅", str(stats["completed_jobs"]), "Completed translations", "green")
    with cols[2]:
        stat_tile("⚡", "1" if active_job_id else "0", "Active job" + ("s" if queued_count else ""), "blue")
    with cols[3]:
        stat_tile("⏱", _format_duration(stats["total_time_seconds"]), "Total processing time", "amber")

    st.write("")
    left, right = st.columns([2, 1], gap="large")

    with left:
        st.markdown("#### Recent translations")
        entries = history_service.get_all()[:5]
        if not entries:
            with st.container(border=True):
                empty_state("📭", "No translations yet", "Head to the Translator tab to get started.")
        else:
            for e in entries:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 1])
                    with c1:
                        st.markdown(f"**{e.filename}**")
                        st.caption(
                            f"{language_label(e.source_lang)} → {language_label(e.target_lang)} · "
                            f"{e.total_pages} pages · {human_readable_size(e.file_size_mb * 1024 * 1024)}"
                        )
                    with c2:
                        st.markdown(status_badge_html(e.status), unsafe_allow_html=True)
                        st.caption(e.completed_at or e.created_at)
                    with c3:
                        out_paths = list(e.output_paths.values())
                        if out_paths and st.button("Open", key=f"dash_open_{e.job_id}", use_container_width=True):
                            err = open_in_file_explorer(str(__import__("pathlib").Path(out_paths[0]).parent))
                            if err:
                                st.error(err)

    with right:
        st.markdown("#### Quick actions")
        with st.container(border=True):
            if st.button("🌐  New translation", use_container_width=True, type="primary"):
                st.session_state["active_page"] = "translator"
                st.rerun()
            if st.button("📂  Open outputs folder", use_container_width=True):
                err = open_in_file_explorer(str(config.OUTPUTS_DIR))
                if err:
                    st.error(err)
            if st.button("🕓  View full history", use_container_width=True):
                st.session_state["active_page"] = "history"
                st.rerun()

        st.write("")
        st.markdown("#### Engine status")
        with st.container(border=True):
            _render_engine_status()


def _render_recovery_banner() -> None:
    incomplete = checkpoint_manager.list_incomplete_jobs()
    active_id = job_manager.get_active_job_id()
    queued_ids = set(job_manager.get_queued_job_ids())
    incomplete = [c for c in incomplete if c.get("job_id") not in queued_ids and c.get("job_id") != active_id]

    if not incomplete:
        return

    with st.container(border=True):
        st.markdown("##### ⚠️ Interrupted translation detected")
        st.caption(
            "It looks like the app closed before a translation finished. "
            "Your progress was saved — you can pick up right where it stopped."
        )
        for data in incomplete:
            job_id = data.get("job_id", "unknown")
            filename = data.get("original_filename", "Unknown file")
            last_page = int(data.get("last_completed_page", -1))
            total = data.get("total_pages") or "?"
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.write(f"**{filename}** — stopped after page {last_page + 1} of {total}")
            with c2:
                if st.button("Resume", key=f"resume_{job_id}", type="primary", use_container_width=True):
                    job = checkpoint_manager.reconstruct_job(job_id)
                    if job:
                        job_manager.submit_job(job)
                        st.session_state["active_page"] = "progress"
                        st.rerun()
                    else:
                        st.error("Could not reconstruct this job (original file may be missing).")
            with c3:
                if st.button("Discard", key=f"discard_{job_id}", use_container_width=True):
                    checkpoint_manager.delete_checkpoint(job_id)
                    st.rerun()


def _render_engine_status() -> None:
    from core.translator_engine import ArgosTranslatorEngine

    engine = ArgosTranslatorEngine.instance()
    pairs = config.SUPPORTED_LANGUAGE_PAIRS
    any_missing = False
    for source, target in pairs:
        available = engine.is_pair_available(source, target)
        any_missing = any_missing or not available
        icon = "🟢" if available else "🔴"
        st.write(f"{icon} {language_label(source)} → {language_label(target)}")

    if any_missing:
        st.caption("Missing models? Run `python scripts/download_models.py` once (needs internet).")
