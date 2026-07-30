"""ui/theme.py — injects the dark glassmorphism CSS theme and exposes a
small accent-color system controlled from the Settings page.
"""

from __future__ import annotations

import streamlit as st

import config

_ACCENT_MAP = {
    "violet": "#8b6df0",
    "blue": "#5b9dff",
    "green": "#3ecf8e",
    "amber": "#f0a84d",
}

_ACCENT_SOFT_MAP = {
    "violet": "rgba(139, 109, 240, 0.16)",
    "blue": "rgba(91, 157, 255, 0.16)",
    "green": "rgba(62, 207, 142, 0.16)",
    "amber": "rgba(240, 168, 77, 0.16)",
}


def inject_theme(accent_color: str = "violet") -> None:
    css_path = config.ASSETS_DIR / "style.css"
    css = css_path.read_text(encoding="utf-8")

    accent = _ACCENT_MAP.get(accent_color, _ACCENT_MAP["violet"])
    accent_soft = _ACCENT_SOFT_MAP.get(accent_color, _ACCENT_SOFT_MAP["violet"])
    override = f":root {{ --accent: {accent}; --accent-violet-soft: {accent_soft}; }}\n"

    st.markdown(f"<style>{css}\n{override}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{subtitle}</div>', unsafe_allow_html=True)
