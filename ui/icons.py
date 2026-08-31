"""ui/icons.py — Lekha's drawn icon set.

Every icon in the app used to be an emoji. Emoji are not an icon system:
they render in whichever colour font the OS ships, they cannot inherit
text colour, their weights and optical sizes disagree with each other,
and they look different on Windows, macOS and Linux. That is fine in a
chat message and wrong in a product surface, where the icons sit beside
each other in a nav rail and are read as a set.

These are drawn on one grid with one stroke weight, inherit `currentColor`
so they follow the theme, and scale cleanly because they are vectors.

Usage:
    from ui.icons import icon
    st.markdown(icon("globe", 18), unsafe_allow_html=True)
"""

from __future__ import annotations

# One grid, one stroke weight, round caps and joins throughout. Keeping
# every path on a 24-unit box is what makes the set read as a family:
# optical sizes stay consistent when the icons sit together in the rail.
_VIEWBOX = 24
_STROKE = 1.6

# Path data only — no per-icon colour, size or stroke. Those are applied
# uniformly at render time so no single icon can drift from the set.
_PATHS: dict[str, str] = {
    # -- navigation ------------------------------------------------------
    "dashboard": (
        '<path d="M4 13h6V4H4v9Z"/><path d="M14 20h6v-9h-6v9Z"/>'
        '<path d="M14 7h6V4h-6v3Z"/><path d="M4 20h6v-3H4v3Z"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/>'
        '<path d="M12 3.5c2.2 2.3 3.3 5.3 3.3 8.5S14.2 18.2 12 20.5"/>'
        '<path d="M12 3.5C9.8 5.8 8.7 8.8 8.7 12s1.1 6.2 3.3 8.5"/>'
    ),
    "activity": (
        '<path d="M3 12h4l2.5-6.5L14 18l2.2-6H21"/>'
    ),
    "clock": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.2V12l3.2 2"/>'
    ),
    "settings": (
        '<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2.2"/>'
        '<path d="M4 17h6"/><path d="M14 17h6"/><circle cx="12" cy="17" r="2.2"/>'
    ),
    # -- actions ---------------------------------------------------------
    "upload": (
        '<path d="M12 15.5V4.5"/><path d="M7.8 8.7 12 4.5l4.2 4.2"/>'
        '<path d="M4.5 15v3a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3"/>'
    ),
    "download": (
        '<path d="M12 4.5v11"/><path d="M7.8 11.3 12 15.5l4.2-4.2"/>'
        '<path d="M4.5 15v3a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3"/>'
    ),
    "folder": (
        '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h3.9a1.5 1.5 0 0 1 1.2.6l1 1.4H19a1.5 '
        '1.5 0 0 1 1.5 1.5v7.4A1.5 1.5 0 0 1 19 18.5H5a1.5 1.5 0 0 1-1.5-1.5V7.5Z"/>'
    ),
    "play": ('<path d="M8 5.5 18.5 12 8 18.5v-13Z"/>'),
    "stop": ('<rect x="6.5" y="6.5" width="11" height="11" rx="2.2"/>'),
    "refresh": (
        '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.5V10h-5.5"/>'
    ),
    "plug": (
        '<path d="M9 3.5v5"/><path d="M15 3.5v5"/>'
        '<path d="M6.5 8.5h11v3a5.5 5.5 0 0 1-11 0v-3Z"/><path d="M12 17v3.5"/>'
    ),
    "trash": (
        '<path d="M4.5 7h15"/><path d="M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7"/>'
        '<path d="M6.5 7l.9 11.1A1.5 1.5 0 0 0 8.9 19.5h6.2a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/>'
    ),
    "swap": (
        '<path d="M4 8.5h13"/><path d="M13.5 5 17 8.5 13.5 12"/>'
        '<path d="M20 15.5H7"/><path d="M10.5 12 7 15.5 10.5 19"/>'
    ),
    # -- status ----------------------------------------------------------
    "check": ('<path d="M5 12.5 9.5 17 19 7.5"/>'),
    "check-circle": ('<circle cx="12" cy="12" r="8.5"/><path d="M8.2 12.2 11 15l4.8-5.4"/>'),
    "alert": (
        '<path d="M12 4.8 20.5 19H3.5L12 4.8Z"/><path d="M12 10v3.6"/>'
        '<circle cx="12" cy="16.4" r=".55" fill="currentColor" stroke="none"/>'
    ),
    "x-circle": ('<circle cx="12" cy="12" r="8.5"/><path d="M9.3 9.3l5.4 5.4"/><path d="M14.7 9.3l-5.4 5.4"/>'),
    "info": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M12 11.2v5"/>'
        '<circle cx="12" cy="8.2" r=".6" fill="currentColor" stroke="none"/>'
    ),
    # -- domain ----------------------------------------------------------
    "file-text": (
        '<path d="M13.5 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 '
        '1.5 0 0 0 1.5-1.5V8.5l-5-5Z"/><path d="M13.5 3.5v5h5"/>'
        '<path d="M9 13h6"/><path d="M9 16.5h4"/>'
    ),
    "pages": (
        '<path d="M8.5 3.5h7L19 7v10.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 7 17.5V5A1.5 1.5 0 0 1 8.5 3.5Z"/>'
        '<path d="M4.5 7v12A1.5 1.5 0 0 0 6 20.5h9"/>'
    ),
    "timer": (
        '<circle cx="12" cy="13.5" r="7"/><path d="M12 10v3.5l2.3 1.6"/><path d="M9.5 3.5h5"/>'
    ),
    "shield": (
        '<path d="M12 3.8 19 6.3v5.2c0 4.2-2.8 7.2-7 8.7-4.2-1.5-7-4.5-7-8.7V6.3l7-2.5Z"/>'
    ),
    "cloud": (
        '<path d="M7.2 18.5A3.7 3.7 0 0 1 7 11.1a5.2 5.2 0 0 1 10-1.3 3.9 3.9 0 0 1-.4 8.7H7.2Z"/>'
    ),
    "sparkle": (
        '<path d="M12 4.2 13.7 9l4.8 1.7-4.8 1.7L12 17.2l-1.7-4.8L5.5 10.7 10.3 9 12 4.2Z"/>'
        '<path d="M18.6 15.4l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z"/>'
    ),
    "search": ('<circle cx="11" cy="11" r="6.5"/><path d="M15.8 15.8 20 20"/>'),
    # -- theme -----------------------------------------------------------
    "sun": (
        '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.8v2.2"/><path d="M12 19v2.2"/>'
        '<path d="M4.6 4.6 6.2 6.2"/><path d="M17.8 17.8l1.6 1.6"/><path d="M2.8 12H5"/>'
        '<path d="M19 12h2.2"/><path d="M4.6 19.4 6.2 17.8"/><path d="M17.8 6.2l1.6-1.6"/>'
    ),
    "moon": ('<path d="M19.5 14.4A8 8 0 0 1 9.6 4.5a8.2 8.2 0 1 0 9.9 9.9Z"/>'),
    "contrast": (
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path d="M12 3.5a8.5 8.5 0 0 1 0 17v-17Z" fill="currentColor" stroke="none"/>'
    ),
}


def icon(name: str, size: int = 18, extra_class: str = "") -> str:
    """Returns an inline SVG string for `name`, sized in px.

    Unknown names return an empty string rather than raising: a missing
    glyph should never be able to take a page down.
    """
    path = _PATHS.get(name)
    if path is None:
        return ""
    classes = f"lk-icon {extra_class}".strip()
    return (
        f'<svg class="{classes}" width="{size}" height="{size}" '
        f'viewBox="0 0 {_VIEWBOX} {_VIEWBOX}" fill="none" '
        f'stroke="currentColor" stroke-width="{_STROKE}" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true" focusable="false">'
        f"{path}</svg>"
    )


def icon_data_uri(name: str) -> str:
    """Returns the icon as a `url(...)` value suitable for a CSS mask.

    Streamlit's button labels accept plain text only, so an icon cannot be
    placed inside one as markup. Masking is what lets the *same* authored
    set appear on buttons as everywhere else: the mask supplies the shape
    and `background: currentColor` supplies the colour, so a masked icon
    follows the theme and the button's own hover and active states for
    free. An image element or a plain background-image would do neither —
    both paint fixed pixels that ignore the surrounding text colour.
    """
    path = _PATHS.get(name)
    if path is None:
        return "none"

    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {_VIEWBOX} {_VIEWBOX}' "
        f"fill='none' stroke='%23000' stroke-width='{_STROKE}' "
        "stroke-linecap='round' stroke-linejoin='round'>"
        f"{path}</svg>"
    )
    # Only the characters that actually break a CSS url() need escaping;
    # over-encoding bloats the rule and hurts nothing but readability.
    svg = (
        svg.replace("#", "%23")
        .replace("<", "%3C")
        .replace(">", "%3E")
        .replace('"', "'")
        .replace("\n", "")
    )
    return f"url(\"data:image/svg+xml,{svg}\")"


def button_icon_css(selector: str, name: str, size: int = 18) -> str:
    """CSS that prefixes a Streamlit button with a masked icon."""
    return (
        f'{selector}::before{{content:"";width:{size}px;height:{size}px;flex:none;'
        f"background:currentColor;-webkit-mask:{icon_data_uri(name)} center/contain no-repeat;"
        f"mask:{icon_data_uri(name)} center/contain no-repeat;}}"
    )


def available() -> list[str]:
    """Icon names, for tests that assert the set stays complete."""
    return sorted(_PATHS)
