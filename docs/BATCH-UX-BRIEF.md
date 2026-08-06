# Batch UX — workspace chrome: results layout, compact tabs, settings, command palette

Repo: `.`, branch `freeform-simplify` (already checked out; other lanes have uncommitted edits under `server/` and `frontend/src/viewport/` — leave every file outside your path list alone).

Frontend: `cd frontend && npm test` (baseline 193 passed) and `npx tsc --noEmit`. Build with `npm run build`. A dev server for this branch is already running on **http://127.0.0.1:3111** — do not start or stop servers; the overseer live-verifies.

**Path discipline — you may only modify:**
- `frontend/src/shell/**`
- `frontend/src/prefs/**`
- `frontend/src/styles/**`
- `frontend/src/design/ParamPanel.tsx`, `frontend/src/design/ParamPanel.test.tsx`, `frontend/src/design/paramPanel.css`
- `frontend/src/App.tsx`
- new files under `frontend/src/shell/` or `frontend/src/prefs/`

Do NOT touch `frontend/src/viewport/**`, `frontend/src/design/SolveOptionsSections.tsx`, `frontend/src/design/parameterRegistry.ts`, `frontend/src/stores/**`, `frontend/src/jobs/**`, `server/**`. Do not run git commands that write; the overseer commits.

Everything below comes from the user's own review of the running app. Where he is describing a symptom rather than a fix, the symptom is the requirement.

---

## 1. Results: far too many panels, no way to close one

> "There are way too many screens, different results. Now there are like six different small windows... every window is very small. The directivity map is the most important thing for me, but maybe some other users will want to look at something else. I would like to be able to close each window — like an X button in the top right corner — and then you can just select how many windows."

`ResultsPanel.tsx` hard-codes six cards into a two-column, three-row grid (`gridTemplateRows: 'repeat(3, …)'`, `gridColumn: index % 2 ? '7 / 13' : '1 / 7'`) and the only control is a chart-type dropdown per card.

- Make the panel count a real, persisted preference: **1, 2, 3, 4 or 6 charts**, with a layout that actually suits each count (1 = full bleed; 2 = side by side; 3 = one large plus two; 4 = 2×2; 6 = current 2×3). One chart in a small pane must still be readable — the whole complaint is that six panels are unreadably small.
- Every card gets a **close button in its top-right corner** that removes that card and reflows the rest. Closing the last card is not a dead end: show an obvious way back (an "add chart" affordance).
- Adding a chart appends a card, up to the maximum. The chart-type dropdown stays.
- `preferences.chartTypes` is currently a fixed-length-6 array with `setChartType(index, …)`. Rework the store so the list is genuinely variable-length, keep `normalize()` honest, and **migrate stored preferences** — bump `STORAGE_VERSION` (currently 2, and there is already a v1→v2 migration to preserve; extend the pattern rather than replacing it) and carry the user's existing chart choices forward, truncated to the new count. Do not reset unrelated settings.
- Keep the existing default six charts as the default selection so nothing changes for a first-time user beyond gaining the controls.

## 2. Panel headers eat a whole row each

> "Above each panel I've got Parameters, and this viewport, and this result, and jobs, each taking up a bit of space... it shouldn't take up this whole row above each panel. It should just be like this one box that says Parameters."

The dockview tab strip (`app.css` `.dv-tabs-and-actions-container`, 29px, full width, bottom-bordered) reads as a title bar. Make it read as a **compact tab chip** instead: the tab itself is a small rounded box sized to its label, the strip loses its full-width divider treatment, and the reclaimed space goes to content. Keep it obviously a tab — it is still the drag handle and the group switcher — and keep the active/inactive states legible in both themes. Trim the strip height where you can without breaking dockview's drag affordance.

## 3. Parameters splits into Geometry and Simulation panels

> "It says Parameters, then below that we have the Geometry and the Simulation tabs. I'm wondering if these should be separate — geometry parameters and simulation parameters up there."

Decision taken: **make them two dockview panels in one group**, so the dock's (now compact) tab row is the switcher and the in-panel tab strip disappears — one header row instead of two.

- `Workspace.tsx`: replace the single `parameters` component with `geometry` and `simulation`, both rendering `ParamPanel` with a `tab` prop. They share one group (the group that used to hold `parameters`), with Geometry active by default.
- **Bump `LAYOUT_KEY`** (`wg2.dockview.layout.v1`) — a stored layout referencing the old `parameters` panel must not be restored into the new component map. A stale layout should fall back cleanly to the new default, not throw.
- `ParamPanel.tsx`: takes the tab as a prop, drops the internal `role="tablist"` strip and its keyboard handling, and drops the `wg-param-active-tab` persistence (dockview now persists which tab is active). Keep everything else: the sections, the collapse state, and the per-panel filter box — the user explicitly likes the search box with labels and keys.
- Remove the `panel-meta` line entirely (`<span className="pill accent">{design.formula}</span><span>complete design inventory</span>`): "you can remove the formula, the R-OSSE, from the design inventory thing, because it's really plain and obvious." The family is already chosen in the Model Type section right below it.
- Update `ParamPanel.test.tsx` for the new shape.

## 4. Small text is unreadable in dark mode

> "Looking at this in dark mode, it's really hard to read the text, especially this small text that says 'Select the horn family that defines the profile equation and its primary dimensions'. It's hard to read because it's so close to the background colour."

Section description text and its siblings are drawn in the dimmest greys (`--fg3`/`--fg4` on the dark surface). Audit the small/muted text in the parameter panel, the preferences surfaces and the results/jobs chrome, and raise contrast so body-sized-and-smaller text clears **WCAG AA (4.5:1)** against the surface it sits on in *both* themes. Prefer fixing the token values and the token *choice* per role over sprinkling one-off colours. State in your final message which tokens you changed and the measured contrast ratios before and after for the worst offenders.

## 5. The top search box does nothing, and ⌘K is stolen by the browser

> "The search at the top — not the one under Geometry or Simulation, the one at the top — I can't get anything when I hit search. Pressing ⌘K just opens the browser's search, which is not what I want."

`TopBar.tsx` renders `<button className="command-affordance">` with no handler at all. Build the real thing:

- A **command palette**: opens on click and on ⌘K/Ctrl-K with `preventDefault()` so the browser never sees it; closes on Escape and on backdrop click; arrow keys and Enter navigate and run; focus returns to where it came from.
- It searches across, at minimum: **parameters** (label, key and symbol — reuse `parameterRegistry`'s existing matcher by importing it read-only; selecting one reveals the owning panel, expands its section and focuses the field), **jobs** (by label — selecting one selects it in the results panel), and **commands** (Solve, Undo, Redo, Open, Save, Save As, Reset layout, Dark/Light theme, Settings, and each results-panel-count option). Group the results by kind, show the ⌘K hint, and show an honest empty state.
- The palette must work when the parameter field lives in a panel that is not currently the active dock tab — activate that panel first.

## 6. A Settings button

> "That needs to be like the settings... I want to take a look under settings. I think they look very good, but have the settings in the top part."

The preference surfaces themselves are good (the user says so) — they are just scattered and undiscoverable. Add a **Settings entry in the top bar** opening a dialog that gathers them in one place: results & export preferences, job preferences, theme, and the results layout count. Reuse the existing `prefs/PreferencesSurface.tsx` controls rather than duplicating them — extract shared pieces if that is what it takes, and keep the inline surfaces working where they are today (the user likes having them in context; this is an addition, not a move). Viewer preferences live in the viewport lane — link to them or leave them out, do not reimplement them.

Dialog quality bar: focus trap, Escape closes, `aria-modal`, restores focus on close.

---

## Definition of done

- `npm test` and `npx tsc --noEmit` pass; report the new test count against the 193 baseline.
- `npm run build` succeeds (the overseer serves `frontend/dist`).
- New tests cover: each panel-count layout renders the right number of cards; closing a card removes it and persists; the preferences migration carries old chart choices forward and does not reset unrelated settings; the palette opens on ⌘K with the browser default prevented, filters across all three kinds, and routes a parameter hit to the correct panel; the settings dialog traps focus and restores it; a stored old-key dock layout falls back to the new default without throwing.
- Report in your final message: what you changed per numbered item, the contrast ratios from item 4, test counts, and anything you found but did not fix.
