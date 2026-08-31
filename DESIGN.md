# Lekha — design system

Recorded from the built interface, not from intention. Where this file and
the code disagree, the code is right and this file is stale.

Lekha is an **Operate** surface: people come here to get a document
translated, not to be impressed. Expression lives in the material and in
precise details; it never obscures the task, the state, or a familiar
affordance.

## The material: Liquid Glass

A pane reads as glass only when four things agree. Take one away and it
reads as a flat translucent rectangle:

| Property | Token | What it does |
|---|---|---|
| Refraction | `--blur`, `--saturate` | `backdrop-filter: blur() saturate()`. Saturation is not optional — blur alone turns the colour behind it to grey mush. |
| Specular | `--specular`, `--specular-soft` | A gradient hairline masked to the border box, brightest along the top edge and gone by 58%. A uniform bright border is a bevel, not a highlight. |
| Body tint | `--glass`, `--glass-strong` | Enough fill that the pane has substance and text stays legible over whatever passes beneath. |
| Cast shadow | `--shadow`, `--shadow-raised` | Offset and softly blurred. A zero-offset halo is decoration, not depth. |

The ground matters as much as the glass: glass with nothing behind it has
nothing to refract. Both themes lay down a fixed, three-field colour wash
(`--ground-wash`) that the panes move over as the page scrolls.

**Nested panes are flattened.** A bordered container inside another
becomes a plain grouping — two stacked sheets of glass destroy the depth
cue that makes the material mean anything.

## Themes

Both are first-class. Dark is the default because of the use scene, not
category habit: Lekha is usually left running for long stretches, often
overnight.

Streamlit reads its own theme once at server start and offers no runtime
switch, so the palette is emitted **server-side** as a `:root` block by
`ui/theme.py`. `assets/style.css` is purely structural and identical in
both themes — there is exactly one place a palette can drift.

| | Dark | Light |
|---|---|---|
| Ground | `#07080d` | `#e9ecf4` — deliberately not white; white gives glass nothing to refract |
| Glass | white @ 5.5% | white @ 62% — over a light ground a pane needs more body before text sits on it |
| Text / muted | `#f2f4f9` / `#7d8497` | `#141824` / `#565d70` |

**Contrast is measured, not eyeballed.** Every text token was sampled
against its composited background in the running app. Worst case: 5.35:1
(dark), 5.56:1 (light), both above the 4.5:1 body-text floor. Light's
muted value was corrected from `#697086`, which measured 4.17:1 and
failed.

## Colour strategy: Restrained

Neutrals plus one accent. The accent marks **primary actions, the current
selection, and live state** — never decoration. Four accents ship
(violet default, blue, green, amber), each with two values per theme:

- `--accent` — interactive fill (primary button, active nav, slider thumb)
- `--accent-text` — the same hue retuned for legibility as text on that
  theme's ground. One value cannot do both: a violet that passes on white
  is unreadable on near-black.
- `--accent-soft` / `--accent-border` — derived at runtime from the fill.

Semantic state colours (`--success`, `--warning`, `--danger`, `--info`)
each ship with a `-soft` companion for badge fills. Inactive states never
carry a saturated fill.

## Typography

One family — the platform UI stack (`--font-sans`). Product UI does not
need a display/body pairing.

Fixed rem scale at roughly a 1.15 ratio (`--t-xs` 0.75rem → `--t-2xl`
1.6875rem). **Not fluid**: users sit at consistent DPI, and a heading that
shrinks inside a column looks broken rather than responsive. More space
above a heading than below it. Prose caps at 68ch; data may run denser.

Tabular numerals on anything that changes in place — metrics, stat
values, the log console — so figures do not reflow as they update.

## Icons

One drawn set, `ui/icons.py`: a 24-unit grid, 1.6 stroke, round caps and
joins, `currentColor`. **No emoji anywhere in the UI.** Emoji render in
whichever colour font the OS ships, cannot inherit text colour, and
disagree with each other on weight and optical size — fine in a message,
wrong in a nav rail where they are read as a set.

Streamlit button labels accept plain text only, so buttons that need an
icon get it via **CSS mask** (`button_icon_css`): the mask supplies the
shape, `background: currentColor` supplies the colour, so the icon follows
the theme and the button's own hover/active states. Buttons that read
better as words simply use words — a second icon family would cost more
than it returns.

## Motion

Product loads into a task; nobody wants to watch it arrive. There are no
page-load sequences and no decorative motion.

- Transitions 180ms on `cubic-bezier(0.22, 1, 0.36, 1)`.
- Press reads as the surface taking the load (`translateY(1px)`), not a bounce.
- Two animations exist and **both report state**: the progress bar's sheen
  (work is happening) and the running badge's pulse (this job is live).
- `prefers-reduced-motion` collapses all of it.

## Layout

Responsive behaviour is **structural**, never fluid type. Streamlit's
columns are a flex row that never wraps, which collapses into clipped
labels and overlapping controls in a narrow window; below 1180px the row
wraps with a 210px minimum so columns stack instead of squeezing past
their content.

## Browser surfaces

The parts nobody draws still carry the design: text selection, caret,
scrollbar, and focus ring are all themed from the palette. Focus is one
treatment everywhere — 2px accent outline, 2px offset — so a keyboard
user gets the same affordance on a button, a field and a tab.

## Component states

Every interactive component ships default, hover, focus, active and
disabled. Empty states teach the next step ("Open Translator, drop in a
PDF") rather than announcing absence. `.lk-skeleton` exists for loading
regions with a known shape — spinners in the middle of content are not
used.

## Where things live

| File | Owns |
|---|---|
| `ui/theme.py` | Both palettes, accent maths, token emission, direction contract |
| `assets/style.css` | All structure; no colour literals |
| `ui/icons.py` | The drawn set + mask helper |
| `ui/components.py` | Rail, stat tiles, badges, empty states, log console, section headings |

The direction contract is emitted as an HTML comment into the running
page, so it can be audited in the browser and not only in the repo.
