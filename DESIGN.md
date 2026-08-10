---
name: Waveguide Generator
description: A dark instrument console for parametric acoustic waveguide design and BEM simulation.
colors:
  accent-cyan: "rgb(87, 216, 236)"
  accent-cyan-deep: "#2ea4bd"
  accent-well: "#0d2f38"
  signal-blue: "rgb(111, 157, 255)"
  signal-violet: "rgb(185, 139, 246)"
  signal-amber: "rgb(242, 181, 68)"
  signal-green: "rgb(90, 212, 141)"
  signal-red: "rgb(255, 111, 96)"
  canvas-void: "#070a0e"
  surface-panel: "#0d1219"
  surface-raised: "#121923"
  hair-soft: "#151b25"
  hair: "#1d2733"
  hair-strong: "#344150"
  ink: "#e9eef6"
  ink-secondary: "#a4b1c4"
  ink-tertiary: "#929eb0"
  ink-quaternary: "#8995a7"
  paper-canvas: "oklch(97% 0.012 78)"
  paper-panel: "oklch(99% 0.005 78)"
  paper-ink: "oklch(18% 0.02 268)"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "normal"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.35
    fontFeature: "tnum 1"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 660
    letterSpacing: "0.1em"
  readout:
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, 'DejaVu Sans Mono', Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    fontFeature: "tnum 1"
  micro:
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, 'DejaVu Sans Mono', Consolas, monospace"
    fontSize: "11px"
    fontWeight: 400
    letterSpacing: "0.04em"
rounded:
  hairpin: "4px"
  control: "5px"
  panel: "6px"
  pill: "999px"
spacing:
  "2": "2px"
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "12": "12px"
  "16": "16px"
  "24": "24px"
components:
  button-solve:
    backgroundColor: "{colors.accent-cyan}"
    textColor: "#03222a"
    rounded: "{rounded.control}"
    padding: "0 9px 0 12px"
    height: "32px"
    typography: "{typography.title}"
  button-icon:
    backgroundColor: "transparent"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.control}"
    size: "32px"
  chip-file:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 9px"
    height: "32px"
  input-number:
    backgroundColor: "rgba(255, 255, 255, .045)"
    textColor: "{colors.ink}"
    rounded: "{rounded.hairpin}"
    padding: "0 7px"
    height: "26px"
    typography: "{typography.readout}"
  card-panel:
    backgroundColor: "{colors.surface-panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "0"
  card-run:
    backgroundColor: "rgba(255, 255, 255, .045)"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "10px 11px 10px 13px"
  badge-state:
    backgroundColor: "rgba(90, 212, 141, .12)"
    textColor: "{colors.signal-green}"
    rounded: "{rounded.pill}"
    padding: "3px 7px 3px 6px"
    typography: "{typography.label}"
---

# Design System: Waveguide Generator

## Overview

**Creative North Star: "The Anechoic Console"**

This is a dark room with an instrument in it. The application is a single
window that never navigates — a console the user sits in front of for hours,
watching a shape and a set of curves respond to numbers they turn. The design
language follows from that: the chrome is a matte, near-black cabinet made of
hairlines and tonal steps, and everything that carries information — the 3D
model, the plots, the numerals — is the only thing allowed to be bright. The
cyan accent behaves like a backlit indicator on real measurement hardware:
sparse, cool, and always attached to something the system actually knows.

Density is the aesthetic, not a compromise against it. A 32px control height,
an 11–13px type range, and 3/6/8/12/16px spacing steps let ~119 parameters and
~28 runs coexist on one screen without the user scrolling to compare. What
keeps that dense grid legible is not whitespace but rank: uppercase tracked
micro-labels for section names, monospace tabular figures for every value, a
prose name that ellipsizes while its ATH formula symbol never does, and a
hairline that separates rather than a card that encloses. Cards are used
exactly once — for a run in the jobs rail — because a run is genuinely a
discrete object. Nothing else gets a box.

The light theme is not an inversion. It uses the original WG v1 warm-paper
palette (`oklch(97% 0.012 78)`) with the same instrument grammar, for users
working in a bright room; it is a second first-class design, not a courtesy.

**Key Characteristics:**
- Near-black cabinet, hairline structure, no nested cards
- One cool accent used as an indicator, never as decoration
- Monospace tabular numerals for every value the system computes
- Uppercase tracked micro-labels as the only "heading" idiom
- Dense by design: 32px controls, 11–13px type, 3–16px spacing
- Two complete themes, dark ink and warm paper

## Colors

A near-monochrome cool-neutral cabinet, lit by one cyan indicator and a small
set of saturated signal hues reserved for state and for plotted series.

### Primary
- **Instrument Cyan** (`rgb(87, 216, 236)`): the accent. It marks the Solve
  action, the active/selected state, a value being edited, a live series, and
  the accent bar on the selected run. Nothing decorative uses it.
- **Cyan Deep** (`#2ea4bd`): the darker end of the Solve gradient and the
  progress fill, so a filling bar reads as motion within one hue.
- **Accent Well** (`#0d2f38`): the recessed cyan ground behind a scrub tooltip
  — a lit readout sunk into the cabinet.

### Secondary
The signal set. Each of these means one thing and is never used for emphasis.
- **Live Green** (`rgb(90, 212, 141)`): the preview is current; a run finished;
  a resolved automatic mode.
- **Stale Amber** (`rgb(242, 181, 68)`): unsaved work, a paused or stale
  preview, a geometry warning, a reconnecting socket. Amber is "attention",
  never "error".
- **Fault Red** (`rgb(255, 111, 96)`): an invalid field, a failed run, a
  backend error. Red appears only where something actually failed.
- **Series Blue** (`rgb(111, 157, 255)`) and **Series Violet**
  (`rgb(185, 139, 246)`): plotted comparison series and the engine badge.

### Neutral
- **Canvas Void** (`#070a0e`): the page ground behind the edge-to-edge console.
- **Surface Panel** (`#0d1219`) / **Surface Raised** (`#121923`): the two
  tonal steps a panel and an active tab occupy. There is no third step.
- **Hairlines** (`#151b25` soft, `#1d2733` default, `#344150` strong): the
  entire structural vocabulary. Soft divides inside a panel, default bounds a
  panel, strong marks hover and an active edge.
- **Ink ramp** (`#e9eef6` → `#a4b1c4` → `#929eb0` → `#8995a7`): value, label,
  unit/meta, and de-emphasized meta, in that order.
- **Warm Paper** (`oklch(97% 0.012 78)` canvas, `oklch(99% 0.005 78)` panel,
  `oklch(18% 0.02 268)` ink): the WG v1 light theme's ground, deliberately warm
  so a bright room reads as paper under a lamp rather than as a blown-out
  screen.

The frontmatter carries the dark theme's values, which are the system's
canonical ones. The light theme re-derives each signal hue against paper rather
than reusing it: amber is `#985015` there, not the dark theme's `#f2b544`, which
measured 4.08:1 on the canvas and 3.66:1 on the status rail and so failed AA on
exactly the text — unsaved, stale, warning — that must not be missed. A signal
hue is a contrast obligation before it is a colour.

### Named Rules
**The Indicator Rule.** Cyan is an indicator, not a brand color. It is legal on
exactly four things: the primary action, the current selection, a value being
edited, and something the system reports as live. If an element is cyan and the
user cannot name which of those four it is, it is wrong.

**The Signal Rule.** Green, amber, and red are reserved for state the backend
actually reports. They may never be recruited for emphasis, category, or
decoration, and no state may be signalled by hue alone — every colored state
also carries a word, a glyph, or a position.

**The Tinted Neutral Rule.** No pure black, no pure gray. Every neutral carries
the cool blue-slate tint in dark and the warm paper tint in light, so the two
themes are two rooms rather than one image and its negative.

## Typography

**UI Font:** the platform system stack (`-apple-system`, `Segoe UI`, …)
**Readout Font:** the platform monospace stack (`SF Mono`, `Menlo`, `Consolas`, …)

**Character:** there is no display face and there never will be. The system
font disappears into the OS, which is correct for a tool; all typographic
personality lives in the monospace readouts and in the uppercase tracked
micro-labels. The body sets `font-variant-numeric: tabular-nums` globally, so
even proportional text keeps its digits in columns.

### Hierarchy
- **Title** (600, 13px, 1.35): the viewport's design name and the one or two
  places the interface names the thing being worked on. Dialog headings take the
  one step above it (18px), the only type in the application with room to lead.
- **Body** (400, 12px, 1.35): parameter names, menu items, and all prose. The
  practical ceiling for prose in this UI is about 60ch inside a popover.
- **Readout** (400, 12px mono, tabular): every number the system computes or the
  user types — field values, timings, element counts, frequencies.
- **Label** (660, 11px, 0.1em tracking, uppercase): section names, panel tabs,
  state badges, and table headers. This is the system's only heading idiom.
- **Micro** (400, 11px mono, 0.04em): units, timestamps, run metadata, status
  bar, and anything the user reads only when they go looking for it.

### Named Rules
**The Tabular Rule.** Any glyph that is part of a measured value is monospace
and tabular. A number in the UI font is a bug wherever two of them might ever
sit in a column.

**The Symbol-Holds Rule.** Where a parameter carries an ATH formula symbol, the
symbol never truncates and the prose name does. Users scan for `(a0)`, not for
"Throat coverage angle."

**The Eleven-Pixel Floor.** The type scale is 11 / 12 / 13 / 18px and nothing
else, and 11px is a floor, not a starting point: no functional text in the
interface is set smaller, whatever its role. Ranks below 13px separate by family
and case -- mono, uppercase, tracking -- rather than by fractional pixels, which
never read as hierarchy anyway. A new size is a request to reconsider the
hierarchy, not to add a step.

## Layout

The shell is a three-row grid — a 48px top rail, a flexible workspace, a 28px
status rail — and the workspace is a dockview grid the user rearranges and
resizes. Panels meet edge-to-edge across one-pixel separators; spacing belongs
inside the information, not around the instrument. Nothing is centered in a
max-width container; every surface fills the space it is given, because the
user chose that space.

Spacing runs on a 4px grid with a 2px half-step — 2 / 4 / 6 / 8 / 12 / 16 / 24
— and nothing between the steps. The tokens are named for their value
(`--space-8` is 8px and can only ever be 8px) rather than for their rank,
because an ordinal scale is what lets a later pass insert a step and silently
move every call site above it. This scale is enforced, not aspirational: an
earlier version of this file claimed 3/6/8/12/16 while the stylesheets used 4,
5, 7, 9, 10, 11, 13, 14, 15, 17, 18 and 20 across 244 declarations, which is
exactly why the interface read as *almost* aligned rather than aligned.

Value controls come in two widths and only two: `--field-w` (86px), sized to the
widest real reading ("140.00 mm"), and `--field-w-wide` (132px) for the things
that genuinely need it — a select whose option names are the point, a text
field, an ATH formula. Panels sit in an 8px gutter with
8px gaps; a parameter row is 30px tall with a 26px control inside it; the run
cards in the jobs rail sit 9px in from the rail edge.

Responsive behavior is about a *narrowing window*, not about phones: this is a
desktop application and there is no mobile layout. At 1100px the wordmark's
version line, the revision chip and the separators leave the top rail and the
5- and 6-chart layouts fall to three rows. At 980px the command affordance
collapses to its icon. At 800px the wordmark itself goes, keyboard hints drop,
and every chart card spans the full dock width. The parameter rail answers to
its *own* width rather than the window's — a container query at 430px pairs
fields into two columns, and formula fields and the quadrant picker opt back
out to full width.

### Named Rules
**The Own-Width Rule.** A panel the user can resize responds to a container
query, not a media query. The window's width is not evidence about a rail the
user just dragged.

**The Density Rule.** A change that fits fewer parameter rows or fewer runs on
screen must buy that space with a specific legibility gain, named at the time.
Padding is not a reason.

## Elevation & Depth

Hybrid, and the split is strict. Anything that is part of the cabinet — panels,
rails, cards, tabs, fields — is flat and separated by hairlines and a single
tonal step. Only things that *float above* the cabinet cast a shadow: menus,
popovers, dialogs, the viewport's glass toolbar, tooltips, and banners. Those
also carry a backdrop blur, so "floating" is signalled twice.

Every raised surface adds a 1px inset top highlight (`--edge-hi`), which in dark
is a 2.8% white and in light is an 85% white. That hairline is what makes the
cabinet read as milled rather than drawn.

### Shadow Vocabulary
- **Card** (`0 12px 34px -22px #000`): panels and the dock's groups. Deep,
  tight, and almost invisible — it separates without lifting.
- **Float** (`0 14px 34px -18px #000`): menus, popovers, dialogs.
- **Tip** (`0 14px 30px -16px #000`): tooltips and scrub readouts.
- **Glass** (float + inset highlight, with `blur(12–18px) saturate(1.25)`): the
  viewport toolbar, the empty-state card, the preference popovers.
- **Viewport vignette** (`0 0 130px -30px rgba(0,0,0,.9) inset`): the only
  shadow that is atmosphere rather than structure, and it is on the one surface
  that is a scene rather than a panel.

### Named Rules
**The Flat Cabinet Rule.** If it is bolted down, it is flat. Shadow is reserved
for surfaces that genuinely occlude what is behind them, and every one of them
also blurs its backdrop.

## Shapes

One radius family, small and consistent: 4px inside a control, 5px on a
control, 6px on a panel or popover, and a full pill (999px) on exactly two
things — state badges and the results dock's chips. Radii do not grow with the
element; a 760px dialog and a 26px field are both 6px and 4px respectively,
which is what keeps a dense grid from looking like a stack of lozenges.

Borders are 1px and always a hairline token; there are no 2px borders in the
system except the 2px left rule that marks a run's state on its card and a
summary metric in the results grid. Circles appear only as status dots (5–6px)
and as the quadrant picker's crosshair.

## Components

### Buttons
- **Shape:** 5px radius (`{rounded.control}`), 32px tall in the top rail, 26–28px
  in panels, 21px in the results toolbar.
- **Solve (primary):** the only filled button in the application — a cyan
  gradient with a dark cyan-black label, an inset top highlight and a mostly
  neutral drop shadow carrying a trace of its own hue (a saturated bloom here is
  the generic glowing-CTA tell, not what a machined control does). There is exactly one on screen and it is the only place the user
  spends compute.
- **Icon:** transparent at rest, `--ov3` wash and full-strength ink on hover, a
  32px square hit area for a 15px glyph.
- **Ghost / panel:** a hairline border over the control gradient, secondary ink.
  Hover raises the border to `hair-strong` and the ink to full.
- **Focus:** every control shares one treatment — a 2px cyan outline at 2px
  offset plus a 4px halo. It is global and must not be overridden per component.

### Chips
- **State badges** (live / stale / paused / error / imported): a pill with a
  12–13% tint of its own hue, a 32–38% border of the same hue, uppercase 680
  weight at 0.14em, and a 5px dot of `currentColor`. The dot plus the word is
  what keeps the state legible without hue.
- **Result chips:** a 21px pill in the results toolbar carrying a 12px line
  swatch in the series' own color, then an ellipsizing name. These are the
  elastic items in that toolbar; everything else holds its size.

### Cards / Containers
- **Panels** are not cards: a 6px radius, a `hair` border, the panel surface,
  the card shadow and the inset highlight — and no internal padding of their
  own. Their contents own their gutters.
- **Run cards** are the one true card, at 6px with a 2px left state rule
  (gray/cyan/green/red). An unselected finished run collapses to a single
  transparent line — name, stars, time — so the rail reads as a list of names
  with one open run in it.
- **Result cards** are chart frames: 4px radius, panel surface, no shadow, and
  their title and actions float *inside* the margin the chart already reserves
  rather than stacking a header above the plot.

### Inputs / Fields
- **Number control:** the signature input. 86px wide, 26px tall, no border at
  rest, a 4.5% white ground, a right-aligned monospace value, a small unit in
  tertiary ink, and a 1px fill track along the bottom edge showing where the
  value sits in its range. The label beside it is a horizontal scrub handle
  (`cursor: ew-resize`).
- **States:** hover raises a `hair-strong` border; editing turns the border and
  the digits cyan over a 9% cyan ground; invalid turns them red and fills the
  track solid red. Every state changes at least two properties, never hue alone.
- **Selects and text inputs:** the control gradient, a `hair` border, a 4px
  radius, and the same 28–32px heights as the buttons beside them.

### Navigation
There is no navigation. The dock's tabs are the only tab-like element — 24px,
uppercase 10px at 0.1em, transparent at rest, and on the active tab a
`surface-raised` ground with a `hair` border and the inset highlight. Single-
purpose groups (viewport, results, jobs) hide their tab row entirely, because a
tab that never changes anything is chrome.

### Signature component: the state badge row
The viewport's top-right corner carries the application's most important claim —
whether what the user is looking at is current. LIVE / STALE / PAUSED /
RECONNECTING / ERROR / IMPORTED are a single family of pills with one geometry
and one hue each, followed by monospace timings. This row is the reason the
signal palette exists, and it is the pattern any new "is this real?" indicator
must copy rather than reinvent.

## Do's and Don'ts

### Do:
- **Do** put every computed number in the monospace readout style with tabular
  figures, at 11px for meta and 12px for values.
- **Do** separate with a 1px hairline and one tonal step. Reach for
  `--hair-soft` inside a panel and `--hair` at its boundary.
- **Do** give every state a word or a glyph in addition to its hue, following
  the state badge row's dot-plus-label pattern.
- **Do** define every new color as a `--*-rgb` triple in `tokens.css` and use
  `rgba(var(--x-rgb), α)` for tints, so both themes get the tint for free.
- **Do** ship every new surface in both themes in the same change, and check
  the warm paper theme is warm — a neutral gray in light mode is a bug.
- **Do** use a container query when the element lives in a rail the user can
  resize.
- **Do** keep the type scale at 11 / 12 / 13 / 18px and the spacing scale at
  2 / 4 / 6 / 8 / 12 / 16 / 24px, and never set functional text below 11px.
- **Do** size every value control from `--field-w` (86px) or `--field-w-wide`
  (132px). A control that picks its own number is how two fields both called
  "Sweep start", one above the other in the same rail, ended up different sizes.

### Don't:
- **Don't** nest a card inside a card, or wrap a group of fields in a box. The
  hairline and the section label are the grouping.
- **Don't** add a second filled button. Solve is the only one.
- **Don't** use cyan for anything that is not the primary action, the selection,
  an edit in progress, or a live state.
- **Don't** use pure `#000`, pure `#fff`, or an untinted gray anywhere.
- **Don't** put a shadow on a surface that does not float, and don't float one
  without a backdrop blur.
- **Don't** animate with bounce or elastic easing, and don't animate anything
  that is not a state change the user caused; motion here is confirmation, not
  personality.
- **Don't** override the global focus ring per component.
- **Don't** buy whitespace with visible rows. See The Density Rule.
- **Don't** invent a mobile layout. Narrow means a small window on a desktop.
