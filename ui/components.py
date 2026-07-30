"""ui/components.py — small, reusable UI building blocks shared across
pages: stat tiles, status badges, the sidebar nav, log consoles, etc.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from models.enums import JobStatus

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("dashboard", "🏠", "Dashboard"),
    ("translator", "🌐", "Translator"),
    ("progress", "⚡", "Progress"),
    ("history", "🕓", "History"),
    ("settings", "⚙️", "Settings"),
]


def render_sidebar(active_page: str) -> str:
    """Renders the sidebar brand + nav and returns the (possibly new)
    active page key based on what the user clicked.
    """
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="logo">📜</div>
                <div>
                    <div class="title">Lekha</div>
                    <div class="subtitle">Offline PDF Translator</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        selected = active_page
        for key, icon, label in NAV_ITEMS:
            is_active = key == active_page
            wrapper_class = "nav-active" if is_active else ""
            st.markdown(f'<div class="{wrapper_class}">', unsafe_allow_html=True)
            if st.button(f"{icon}  {label}", key=f"nav_{key}", use_container_width=True):
                selected = key
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='margin-top:auto'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='position:fixed; bottom:18px; left:24px; font-size:11px; color:#6b6f80;'>"
            "v1.0.0 · 100% local & offline</div>",
            unsafe_allow_html=True,
        )

    return selected


def stat_tile(icon: str, value: str, label: str, accent: str = "violet") -> None:
    colors = {
        "violet": ("rgba(139,109,240,0.16)", "#8b6df0"),
        "blue": ("rgba(91,157,255,0.16)", "#5b9dff"),
        "green": ("rgba(62,207,142,0.16)", "#3ecf8e"),
        "amber": ("rgba(240,168,77,0.16)", "#f0a84d"),
    }
    bg, fg = colors.get(accent, colors["violet"])
    st.markdown(
        f"""
        <div class="glass-card stat-tile">
            <div class="stat-icon" style="background:{bg}; color:{fg};">{icon}</div>
            <div class="stat-value">{value}</div>
            <div class="stat-label">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


_BADGE_LABELS = {
    JobStatus.QUEUED.value: ("queued", "Queued"),
    JobStatus.RUNNING.value: ("running", "Translating"),
    JobStatus.PAUSED.value: ("queued", "Paused"),
    JobStatus.COMPLETED.value: ("completed", "Completed"),
    JobStatus.FAILED.value: ("failed", "Failed"),
    JobStatus.CANCELLED.value: ("cancelled", "Cancelled"),
}


def status_badge_html(status: str) -> str:
    css_class, label = _BADGE_LABELS.get(status, ("cancelled", status.title()))
    return f'<span class="badge badge-{css_class}"><span class="badge-dot"></span>{label}</span>'


def status_badge(status: str) -> None:
    st.markdown(status_badge_html(status), unsafe_allow_html=True)


def language_label(code: str) -> str:
    import config

    return config.SUPPORTED_LANGUAGES.get(code, code.upper())


def log_console(lines: list[str], height: Optional[int] = None) -> None:
    if not lines:
        st.markdown('<div class="log-console">No log output yet.</div>', unsafe_allow_html=True)
        return
    joined = "\n".join(lines[-300:])
    style = f"max-height:{height}px;" if height else ""
    st.markdown(f'<div class="log-console" style="{style}">{joined}</div>', unsafe_allow_html=True)


def empty_state(emoji: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="emoji">{emoji}</div>
            <div style="font-size:17px; color:#f2f3f7; font-weight:600;">{title}</div>
            <div style="font-size:13.5px; margin-top:4px;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
