"""app.py — Lekha entrypoint.

Run with:
    streamlit run app.py

Wires together: page config, the dark glassmorphism theme, sidebar
navigation, and dispatch to the five top-level pages. Routing state
lives in st.session_state["active_page"] so pages can redirect each
other (e.g. "Start Translation" -> Progress, "Resume" -> Progress).
"""

from __future__ import annotations

import streamlit as st

import config
from services.logger_service import get_logger
from services.settings_service import settings_service
from ui.components import render_sidebar
from ui.pages import dashboard, history, progress, settings as settings_page, translator
from ui.theme import inject_theme

logger = get_logger("app")

st.set_page_config(
    page_title=config.APP_NAME,
    page_icon="📜",
    layout="wide",
    initial_sidebar_state="expanded",
)

_PAGES = {
    "dashboard": dashboard,
    "translator": translator,
    "progress": progress,
    "history": history,
    "settings": settings_page,
}


def main() -> None:
    app_settings = settings_service.get_all()
    inject_theme(app_settings.get("accent_color", "violet"))

    if "active_page" not in st.session_state:
        st.session_state["active_page"] = "dashboard"

    selected = render_sidebar(st.session_state["active_page"])
    if selected != st.session_state["active_page"]:
        st.session_state["active_page"] = selected
        st.rerun()

    page_module = _PAGES.get(st.session_state["active_page"], dashboard)
    try:
        page_module.render()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error rendering page '%s'", st.session_state["active_page"])
        st.error(f"Something went wrong loading this page: {exc}")
        st.caption("Check logs/app.log for details, or use Settings → Export logs.")


main()
