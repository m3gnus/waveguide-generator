# Product

<!-- impeccable:product-schema 1 -->

> **Provenance.** Impeccable's `init` normally interviews the user for these
> facts. The user delegated this session explicitly ("do that all in sequence
> without my interaction"), so every entry below is *inferred* from the
> repository — README.md, docs/, the FastAPI server, and the React frontend —
> rather than confirmed by a person. Entries that the code cannot settle are
> marked **[unconfirmed]** and must be treated as open until the user says
> otherwise.

## Platform

web

## Users

Loudspeaker designers and DIY horn/waveguide builders running the application
locally on their own machine. The primary user is technical: they read
directivity plots, know what a throat radius and a coverage angle are, and
arrive with an intent like "widen the mouth until the 6 dB contour holds to
10 kHz." They work in long sessions, iterating a parameter set against
simulation results, and they compare many runs against each other.

Secondary audience **[unconfirmed]**: ATH (Ath4/AKABAK) users migrating an
existing profile, which is why parameters carry their ATH formula symbols
(`R`, `a`, `a0`, `r0`, `k`, `m`, `b`, `r`, `q`, `tmax`) alongside prose names.

## Product Purpose

Design an acoustic waveguide parametrically, see the geometry in 3D as the
parameters change, run a boundary-element simulation of its acoustic behavior,
and read the result as SPL, directivity, DI, and impedance plots. Success is a
user converging on a geometry they will actually build, and being able to export
it (STEP/mesh/CAD link) and to justify the choice against the runs they rejected.

## Positioning

The mesher is the single geometry authority: the 3D preview, the simulation
mesh, and the CAD export all come from one generator, so what the user sees is
what gets solved and what gets built. Runs are first-class and persistent —
named, rated, compared, re-run, and exported — rather than transient output.
The whole thing runs locally with no account and no cloud round-trip.

## Operating Context

- Local desktop application: a FastAPI server on port 3100 serving a
  React/TypeScript SPA; v1 ships beside it on its own port and data directory.
- Installed by a platform installer that builds a `.venv`, verifies a published
  SHA-256 for the prebuilt interface, and checks that a solve can actually run.
- Windows, macOS (Intel and Apple Silicon, with a Metal solve backend), Linux.
- A session is long-lived and single-window: the user does not navigate between
  pages; they rearrange a dockview workspace of Geometry/Simulation parameters,
  a 3D viewport, a results dock, and a jobs rail.
- Solves take seconds to minutes and run asynchronously; the user keeps working
  while they run, and job state arrives over a WebSocket.
- Work leaves the app as `.cfg`/design files, STEP and mesh exports, chart
  images, and a Fusion 360 CAD link.

## Capabilities and Constraints

- Parametric model families (R-OSSE and others) with ~119 parameters, each
  carrying a prose name, an ATH formula symbol, a unit, and a tooltip.
- Live 3D preview driven over a WebSocket, with a Live/Stale/Paused state the
  user must be able to trust.
- Asynchronous solve jobs with progress, stages, star ratings, per-run export,
  and failure states carrying real backend error text.
- Results dock: up to six chart cards (SPL, directivity H/V heatmaps, DI,
  impedance, summary), a compare set, follow-latest behavior, and a detail modal.
- Two committed themes, dark and light, both already implemented as a full token
  set; the light theme is a warm paper palette, not an inverted dark theme.
- Density is a requirement, not an accident: the user wants many parameters and
  many runs visible at once. Whitespace that costs visible rows is a regression.
- English-only today; no i18n layer exists. **[unconfirmed]** whether that is
  permanent.
- Licensed AGPL-3.0-or-later. Author identity in the repository is `m3gnus`.

## Brand Commitments

- Product name "Waveguide Generator", shown in the top rail as a wordmark with
  the version beneath it.
- The existing visual identity is a dark, instrument-like workspace with a cyan
  accent (`--acc`), hairline borders, monospace numerics, and tabular figures.
  Treat this as the incumbent world to refine, not to replace.
- Voice is the voice of the existing UI copy and the README: precise, plain,
  unhurried, no exclamation marks, no marketing adjectives, and no invented
  claims about accuracy or performance.

## Evidence on Hand

- A running local instance with real designs (`tritonia_mk2.cfg`) and a real job
  history of ~28 runs, so every surface can be evaluated with true content
  rather than placeholders.
- `docs/` carries the design and contract documents, including
  `docs/TRACEABILITY-TABLE.md` and `docs/P6-CUTOVER-PLAN.md`.
- No testimonials, customers, benchmarks, pricing, or adoption numbers exist.
  Future work must not fabricate any.

## Product Principles

1. **The instrument tells the truth.** Every readout, badge, and state chip
   reports something the system actually knows. Nothing decorative may look like
   data, and nothing stale may look live.
2. **Density is the feature.** Scanability comes from alignment, rhythm, and
   typographic rank — not from padding. A change that fits fewer parameters or
   fewer runs on screen has to earn it.
3. **The numbers lead.** Values, units, and symbols are the content; labels and
   chrome recede. Numerics stay monospaced and tabular so columns compare.
4. **Nothing blocks the work.** Solves, exports, and previews run without
   trapping the user; failures explain themselves in place and stay dismissible.
5. **Both themes are first-class.** Dark and light are two real designs, and any
   new surface ships in both or does not ship.

## Accessibility & Inclusion

No product-specific standard has been confirmed **[unconfirmed]**. The
implementation already carries a global `:focus-visible` ring, `prefers-reduced-
motion` handling, and `.sr-only`, which sets the working floor: visible focus on
every interactive control, WCAG AA contrast for text in both themes, full
keyboard reach for anything the pointer can do, and no state signalled by color
alone.
