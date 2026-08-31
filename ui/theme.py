"""ui/theme.py — Lekha's visual world: Liquid Glass, in light and dark.

Why the palette lives in Python rather than in the stylesheet
-------------------------------------------------------------
Streamlit has no runtime theming: `.streamlit/config.toml` is read once at
server start, and there is no way to stamp `data-theme` on the document
root without shipping JavaScript. But the active theme *is* known
server-side — it is a persisted setting — so the honest solution is to
emit the concrete token values for the active theme and let the
stylesheet stay purely structural. One source of truth per theme, no
JavaScript, and no flash of the wrong palette on rerun.

The material
------------
Liquid Glass is not "a card with blur on it". A pane reads as glass only
when four things agree:

  * refraction  — backdrop blur *plus* saturation, so colour bleeds
                  through from the ground rather than turning to grey mush
  * specular    — a bright hairline along the top edge where light catches,
                  fading as the edge turns away from the light
  * body tint   — a slight fill so the pane has substance and text stays
                  legible over whatever passes beneath it
  * cast shadow — offset and softly blurred, never a zero-offset halo

Take any one away and it reads as a flat translucent rectangle. All four
are tokens below, and they differ between themes: dark glass is light
added to darkness, light glass is white frost over colour.

The ground matters as much as the glass. Glass with nothing behind it has
nothing to refract, so both themes lay down a wide, soft colour field for
the panes to sit over.
"""

from __future__ import annotations

import streamlit as st

import config

# ---------------------------------------------------------------------------
# Accents
# ---------------------------------------------------------------------------
# Two values per accent: `fill` carries interactive surfaces (buttons,
# selection, the active nav item) and `text` is the same hue re-tuned for
# legibility as text/iconography against that theme's ground. One value
# cannot do both jobs — a violet that passes on white is too dark to read
# on near-black, and the reverse washes out.
ACCENTS: dict[str, dict[str, str]] = {
    "violet": {"dark_fill": "#7d5cf6", "dark_text": "#b4a1fb",
               "light_fill": "#6d4de0", "light_text": "#5a3ccc"},
    "blue":   {"dark_fill": "#3b82f6", "dark_text": "#8fbcfb",
               "light_fill": "#2563eb", "light_text": "#1d4ed8"},
    "green":  {"dark_fill": "#10b981", "dark_text": "#6ee7b7",
               "light_fill": "#059669", "light_text": "#047857"},
    "amber":  {"dark_fill": "#f59e0b", "dark_text": "#fcd34d",
               "light_fill": "#d97706", "light_text": "#b45309"},
}

THEMES = ("dark", "light")

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
_DARK: dict[str, str] = {
    # Ground — a deep, slightly cool black with two wide colour fields the
    # glass has something to refract.
    "ground": "#07080d",
    "ground-wash": (
        "radial-gradient(1200px 680px at 12% -8%, rgba(125,92,246,.20), transparent 60%),"
        "radial-gradient(1000px 620px at 96% 8%, rgba(37,99,235,.14), transparent 62%),"
        "radial-gradient(900px 700px at 60% 108%, rgba(16,185,129,.08), transparent 60%)"
    ),
    # Glass
    "glass": "rgba(255,255,255,.055)",
    "glass-strong": "rgba(255,255,255,.085)",
    "glass-hover": "rgba(255,255,255,.10)",
    "glass-border": "rgba(255,255,255,.11)",
    "glass-border-strong": "rgba(255,255,255,.19)",
    "specular": "rgba(255,255,255,.42)",
    "specular-soft": "rgba(255,255,255,.14)",
    "shadow": "0 18px 44px -20px rgba(0,0,0,.85), 0 4px 14px -8px rgba(0,0,0,.6)",
    "shadow-raised": "0 30px 70px -28px rgba(0,0,0,.92), 0 8px 22px -12px rgba(0,0,0,.7)",
    "blur": "26px",
    "saturate": "175%",
    # Second neutral layer for the rail — cooler and darker than content.
    "rail": "rgba(10,11,18,.72)",
    "rail-border": "rgba(255,255,255,.07)",
    # Text
    "text": "#f2f4f9",
    "text-secondary": "#aab0c2",
    "text-muted": "#7d8497",
    "text-on-accent": "#ffffff",
    # Fields
    "field": "rgba(255,255,255,.045)",
    "field-border": "rgba(255,255,255,.13)",
    "field-hover": "rgba(255,255,255,.22)",
    # Semantic
    "success": "#34d399", "success-soft": "rgba(52,211,153,.15)",
    "warning": "#fbbf24", "warning-soft": "rgba(251,191,36,.15)",
    "danger": "#f87171",  "danger-soft": "rgba(248,113,113,.15)",
    "info": "#60a5fa",    "info-soft": "rgba(96,165,250,.15)",
    "selection": "rgba(125,92,246,.34)",
    "scheme": "dark",
}

_LIGHT: dict[str, str] = {
    # Ground — not white. White gives glass nothing to refract, and a flat
    # white app under a bright window is the harshest thing on a screen.
    "ground": "#e9ecf4",
    "ground-wash": (
        "radial-gradient(1100px 640px at 10% -10%, rgba(109,77,224,.20), transparent 60%),"
        "radial-gradient(980px 600px at 98% 4%, rgba(37,99,235,.14), transparent 62%),"
        "radial-gradient(900px 680px at 56% 106%, rgba(5,150,105,.10), transparent 60%)"
    ),
    # Glass — white frost. Higher alpha than the dark theme: over a light
    # ground the pane needs more body before text sits comfortably on it.
    "glass": "rgba(255,255,255,.62)",
    "glass-strong": "rgba(255,255,255,.78)",
    "glass-hover": "rgba(255,255,255,.86)",
    "glass-border": "rgba(255,255,255,.85)",
    "glass-border-strong": "rgba(15,23,42,.14)",
    "specular": "rgba(255,255,255,.95)",
    "specular-soft": "rgba(255,255,255,.55)",
    "shadow": "0 16px 40px -20px rgba(23,31,56,.30), 0 3px 10px -6px rgba(23,31,56,.18)",
    "shadow-raised": "0 30px 64px -26px rgba(23,31,56,.36), 0 8px 20px -12px rgba(23,31,56,.22)",
    "blur": "24px",
    "saturate": "180%",
    "rail": "rgba(255,255,255,.60)",
    "rail-border": "rgba(15,23,42,.08)",
    "text": "#141824",
    "text-secondary": "#454c5e",
    # Measured, not eyeballed: over this theme's glass the previous value
    # (#697086) came out at 4.17:1, under the 4.5 floor for body-sized
    # text. Muted is the smallest text in the app, so it is the one value
    # that cannot be picked by feel.
    "text-muted": "#565d70",
    "text-on-accent": "#ffffff",
    "field": "rgba(255,255,255,.80)",
    "field-border": "rgba(15,23,42,.14)",
    "field-hover": "rgba(15,23,42,.28)",
    "success": "#047857", "success-soft": "rgba(4,120,87,.13)",
    "warning": "#b45309", "warning-soft": "rgba(180,83,9,.13)",
    "danger": "#b91c1c",  "danger-soft": "rgba(185,28,28,.12)",
    "info": "#1d4ed8",    "info-soft": "rgba(29,78,216,.12)",
    "selection": "rgba(109,77,224,.24)",
    "scheme": "light",
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}


# The direction this surface was built to. Kept in the emitted markup so
# it survives into the running page and can be audited there, not just in
# the repo.
_DIRECTION_CONTRACT = """
THESIS: A translation desk that shows its work. Lekha refuses the flat
opaque dashboard: panes are glass over a lit ground, so depth encodes
which layer you are on rather than a border doing it alone.
OWN-WORLD: Liquid Glass — refraction (backdrop blur + saturation),
a specular hairline where light catches an edge, a body tint, and an
offset cast shadow. Restrained colour: neutrals plus one accent that only
ever marks a primary action, the current selection, or a state. One drawn
icon set on a 24-unit grid. One sans, fixed rem scale.
STORY: The visitor drops in a PDF, chooses how it should be translated,
watches real progress, and leaves with a document that looks like the one
they started with.
FIRST VIEWPORT: Glass rail left, current page lit by accent. Content
right: title, then the work. Primary action is the only filled accent
surface on screen.
FORM: Brief-pinned (Apple Liquid Glass, light and dark). No direction
roll — a pinned brief beats the roll.
FINISH: unreviewed and undocumented is unfinished; this build ends with
the finish review, the verdict, DESIGN.md, and every shipping raster
carrying its provenance.
"""


def resolve_theme(name: str) -> str:
    return name if name in _PALETTES else "dark"


def resolve_accent(name: str) -> str:
    return name if name in ACCENTS else "violet"


def _rgba(hex_color: str, alpha: float) -> str:
    """Hex to rgba(). The soft accent has to be translucent rather than a
    pre-mixed tint, so it works over whichever ground and glass sit behind
    it in either theme."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return f"rgba(125,92,246,{alpha})"
    return f"rgba({r},{g},{b},{alpha})"


def build_tokens(theme: str = "dark", accent: str = "violet") -> str:
    """Returns the `:root` block for the active theme and accent."""
    theme = resolve_theme(theme)
    accent = resolve_accent(accent)
    palette = dict(_PALETTES[theme])
    accent_spec = ACCENTS[accent]

    fill = accent_spec[f"{theme}_fill"]
    palette["accent"] = fill
    palette["accent-text"] = accent_spec[f"{theme}_text"]
    # The selected/active wash. Light glass is already close to white, so
    # the tint needs more presence there to register at all.
    palette["accent-soft"] = _rgba(fill, 0.18 if theme == "dark" else 0.14)
    palette["accent-border"] = _rgba(fill, 0.55)
    palette["selection"] = _rgba(fill, 0.30)

    declarations = "".join(f"--{k}:{v};" for k, v in palette.items())
    return f":root{{{declarations}}}"


def inject_theme(accent_color: str = "violet", theme: str = "dark") -> None:
    """Injects the palette, then the structural stylesheet."""
    css_path = config.ASSETS_DIR / "style.css"
    try:
        structure = css_path.read_text(encoding="utf-8")
    except OSError:
        structure = ""

    # Emitted as two separate blocks on purpose. A markdown HTML *comment*
    # block terminates at the line containing "-->", so anything sharing
    # that line — a <style> tag, say — closes with it, and the stylesheet's
    # remaining lines get parsed as markdown and rendered as visible text.
    # A <style> block, by contrast, runs until </style> however many lines
    # it spans, so it has to open its own block.
    st.markdown(
        f"<style>{build_tokens(theme, accent_color)}\n{structure}</style>",
        unsafe_allow_html=True,
    )
    st.markdown(f"<!--{_DIRECTION_CONTRACT}-->", unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    subtitle_html = f'<p class="page-subtitle">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f'<header class="page-head"><h1 class="page-title">{title}</h1>{subtitle_html}</header>',
        unsafe_allow_html=True,
    )
