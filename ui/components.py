"""ui/components.py — the shared vocabulary: rail, tiles, badges, empty
states, log console.

Every component here exists so the same idea looks the same everywhere.
A status reads as a status on the dashboard, in the queue and in history,
because all three call `status_badge_html`, not because three pages
happened to style a span the same way.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

import config
from models.enums import JobStatus
from ui.icons import button_icon_css, icon

# key, icon, label
NAV_ITEMS: list[tuple[str, str, str]] = [
    ("dashboard", "dashboard", "Dashboard"),
    ("translator", "globe", "Translator"),
    ("progress", "activity", "Progress"),
    ("history", "clock", "History"),
    ("settings", "settings", "Settings"),
]


def _nav_css(active_page: str) -> str:
    """Icons for every nav item, plus the active item's treatment.

    Both are emitted here rather than living in style.css because both
    depend on runtime values — which icon belongs to which widget key, and
    which page is current.

    The active rule is anchored to `body` on purpose. This <style> is
    rendered inside the sidebar, which Streamlit places earlier in the DOM
    than the main stylesheet, so at equal specificity the global
    `.stButton button:hover` rule wins on source order and the highlight
    would vanish under the pointer. `:hover` is matched explicitly for the
    same reason.
    """
    rules = [
        button_icon_css(f'[data-testid="stSidebar"] .st-key-nav_{key} button', name)
        for key, name, _ in NAV_ITEMS
    ]

    active = f'body [data-testid="stSidebar"] .st-key-nav_{active_page} button'
    rules.append(
        f"{active},{active}:hover{{"
        "background:var(--accent-soft,var(--glass-hover))!important;"
        "border-color:var(--accent)!important;"
        "color:var(--text)!important;font-weight:650!important;"
        "box-shadow:inset 0 1px 0 var(--specular-soft);}"
    )
    rules.append(f"{active} p,{active}:hover p{{color:var(--text)!important;}}")
    rules.append(f"{active}::before{{color:var(--accent-text);}}")

    # Theme switch and its icon.
    rules.append(button_icon_css('[data-testid="stSidebar"] .st-key-theme_toggle button', "sun", 16))
    rules.append(
        '[data-testid="stSidebar"] .st-key-theme_toggle button{'
        "font-size:var(--t-xs)!important;padding:0.3rem 0.6rem!important;"
        "color:var(--text-muted)!important;}"
    )
    return "<style>" + "".join(rules) + "</style>"


def render_sidebar(active_page: str, theme: str = "dark") -> tuple[str, Optional[str]]:
    """Renders the rail. Returns (selected_page, requested_theme_or_None)."""
    requested_theme: Optional[str] = None

    with st.sidebar:
        st.markdown(
            '<div class="lk-brand">'
            f'<div class="lk-mark">{icon("pages", 19)}</div>'
            '<div><div class="lk-name">Lekha</div>'
            '<div class="lk-tag">Document translation</div></div>'
            "</div>",
            unsafe_allow_html=True,
        )

        st.markdown(_nav_css(active_page), unsafe_allow_html=True)

        selected = active_page
        for key, _icon_name, label in NAV_ITEMS:
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                selected = key

        # The switch names the theme it will move to, not the one you are
        # in: a control is labelled with its action.
        next_theme = "light" if theme == "dark" else "dark"
        st.markdown('<div class="lk-rail-foot"></div>', unsafe_allow_html=True)
        if st.button(
            f"Switch to {next_theme}",
            key="theme_toggle",
            use_container_width=True,
            help="Changes the appearance of the whole app.",
        ):
            requested_theme = next_theme

        st.markdown(
            f'<div class="lk-meta" style="padding:2px 4px">v{config.APP_VERSION} · '
            "Offline by default</div>",
            unsafe_allow_html=True,
        )

    return selected, requested_theme


def stat_tile(icon_name: str, value: str, label: str, tone: str = "accent") -> None:
    """A single figure with its label. Deliberately not a card of icon +
    heading + body text: the number is the content."""
    tones = {
        "accent": ("var(--accent-soft, var(--glass-strong))", "var(--accent-text)"),
        "success": ("var(--success-soft)", "var(--success)"),
        "info": ("var(--info-soft)", "var(--info)"),
        "warning": ("var(--warning-soft)", "var(--warning)"),
    }
    bg, fg = tones.get(tone, tones["accent"])
    st.markdown(
        f'<div class="lk-glass lk-stat">'
        f'<div class="lk-stat-icon" style="background:{bg};color:{fg}">{icon(icon_name, 18)}</div>'
        f'<div class="lk-stat-value">{value}</div>'
        f'<div class="lk-stat-label">{label}</div>'
        "</div>",
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
    css_class, label = _BADGE_LABELS.get(status, ("cancelled", str(status).title()))
    return f'<span class="lk-badge lk-badge-{css_class}"><span class="lk-dot"></span>{label}</span>'


def status_badge(status: str) -> None:
    st.markdown(status_badge_html(status), unsafe_allow_html=True)


def language_label(code: str) -> str:
    return config.SUPPORTED_LANGUAGES.get(code, code.upper())


def log_console(lines: list[str], height: Optional[int] = None) -> None:
    if not lines:
        st.markdown(
            '<div class="log-console">Waiting for the first log line…</div>',
            unsafe_allow_html=True,
        )
        return
    # Escaped because log lines carry filenames and error text straight
    # from disk, and this is rendered as raw HTML.
    import html

    joined = html.escape("\n".join(lines[-300:]))
    style = f"max-height:{height}px;" if height else ""
    st.markdown(f'<div class="log-console" style="{style}">{joined}</div>', unsafe_allow_html=True)


def empty_state(icon_name: str, title: str, subtitle: str = "") -> None:
    """Empty states teach the interface — they say what to do next, not
    that there is nothing here."""
    body = f'<div class="lk-empty-body">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="lk-empty">'
        f'<div class="lk-empty-mark">{icon(icon_name, 22)}</div>'
        f'<div class="lk-empty-title">{title}</div>{body}</div>',
        unsafe_allow_html=True,
    )


def section(title: str, icon_name: str = "") -> None:
    """A section heading in the shared vocabulary."""
    mark = f'{icon(icon_name, 16)}' if icon_name else ""
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;margin:26px 0 10px;'
        f'color:var(--text);font-weight:640;font-size:var(--t-md)">{mark}{title}</div>',
        unsafe_allow_html=True,
    )
