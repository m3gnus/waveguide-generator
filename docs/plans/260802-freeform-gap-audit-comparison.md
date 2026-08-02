# FREEFORM gap audits — Opus vs codex comparison and synthesis

*2026-08-02. Two independent read-only audits of the `freeform` branches ran with
an identical brief: an Opus subagent (`260802-freeform-gap-audit-opus.md`, F1-F16)
and codex `gpt-5.6-sol` (`260802-freeform-gap-audit-codex.md`, items 1-10).
This file compares them and fixes the disagreements into one plan.*

## Where they agree (high confidence — both verified independently)

- **No save/load is the top usability gap**: every file path except STEP is
  closed for FREEFORM; localStorage is the only persistence. `.mwg`
  serializer/parser + export carve-out is the fix, gated on the frontend
  model consolidation.
- **Convert-from-design is the adoption blocker**: switching the model type
  resets to defaults; hand-building anchors is the only on-ramp.
- **The diagnostics channel exists end-to-end and is dropped client-side**
  (`scene.js` discards `metadata.freeform`); make the backend authoritative
  for curves/spans/deviations, keep the JS curve only as an optimistic drag
  preview, delete `computeInflectionSpans`.
- **Consolidate the four frontend point normalizers now** (Opus additionally
  proved they *disagree*: clamp vs ignore vs throw on out-of-range z).
- Export menu items fail as toasts pointing at alternatives that don't exist.
- Optimizer (Phase 3) and sharp corners (1c) remain fully deferred; neither
  blocks the rest.

## Where they disagree — and who is right

1. **Drop `cornerRatio` now?** codex: yes (S). Opus: **no — blocked**, with
   measurements: the M7 active-span fix over-corrected, capping any
   station active over the whole horn at the *throat* radius; the reference
   owner design in mm form (13.2 mm) is **rejected** (cap 12.7 mm) while its
   `cornerRatio: 0.12` equivalent builds. The UI only writes the broken mm
   form; all mesher build tests use ratio. **Opus wins** — fix the span
   validation first (weight-aware or neighbour-span), then migrate tests to
   mm, then drop ratio.
2. **Collapse the mesher's triple feature gate now?** codex: DO-NOW (S).
   Opus: **DO-LATER**, having verified two behavioural deltas a naive
   collapse would lose (expression checks sampled over phi vs phi=0-only;
   gcurve key-presence vs value activity). **Opus wins** — it is a
   behaviour-preservation exercise with zero user benefit; do it when the
   alias surface is next touched.
3. **Convert effort**: codex L (assumes the plan's "dedicated grid request").
   Opus: **M — the raw grid already ships** in every viewport response
   (`payload.grid.inner_points` (n_phi, n_len+1, 3) + exact 0/π2 rows in
   `angle_list`, verified live); it is merely dropped in `useCases.js`.
   **Opus wins**; one-line passthrough unblocks both convert and the inset.
4. **Cross-section inset data source**: codex proposes a new authoritative
   outline atlas in viewport metadata; Opus proposes drawing the already-
   shipped grid ring at the scrubbed t (shows the *sampled* outline that
   actually gets meshed), analytic curve as a ghost. **Opus's is simpler and
   more honest**; adopt it.

## Unique finds worth acting on

**Opus only:** the F2 corner-cap regression (above — the top item); the
convexity guard's usable corner window measured at 5-12.7 mm on a plain
circle→rect design with 2-4 mm corners rejected as "non-convex near t=0.75"
(needs min-feasible-corner in the message + characterization tests — the
rejection path has *no test at all*); a 3570-profile JS-vs-python inflection
sweep (11 badge flips at the 1° threshold + one 33.7° vs 19.2°+14.5°
span-merge disagreement); the "Free" inflection-policy option is a no-op
(three UI options, only *reject* does anything); **zero documentation**
(config-schema.md still lists only the old formulas); ~295 ms cold-scipy
stall on the first FREEFORM request per server process; focusable-but-inert
handle `tabindex`; unthrottled full SVG rebuild in `onPointerMove`.

**codex only:** an epsilon bug at the exact corner floor ("must be in
[2.8, 140] mm … got 2.8"); latency measurements showing the server viewport
build is ~8-14 ms so perceived drag latency is frontend-side; the 6×
acoustic-sampling cost of corner-aware rings (6.8→42 ms) worth a perf
budget; concrete backend STL/per-azimuth-CSV export paths; the missing
real-STEP and one-frequency-BEM acceptance tests.

## Synthesized execution order

1. **Fix the corner-radius span validation** (weight-aware cap + epsilon-safe
   bounds + show the allowed window in the error and UI) — unblocks mm
   corners, convert seeding, and the ratio deletion. *M*
2. **Characterization tests** for the convexity guard + corner window (both
   rejection paths currently untested). *S*
3. **Convert current design to FREEFORM** (grid passthrough + un-scale +
   rollback truncation + decimation + station seeding + deviation report,
   confirm-before-replace). *M*
4. **Frontend model/codec consolidation**, then **`.mwg` round-trip + config
   export carve-out** (+ docs for the format as it lands). *M-L*
5. **Backend-authoritative curves/diagnostics**; delete the JS inflection
   mirror; surface `maxNormalDeviationMm` + profile deviation. *M*
6. **Draggable station lines with mm depth**, then the **cross-section
   scrubber inset** drawn from the shipped grid (shared scrubber machinery). *S + M*
7. **Error-UX pass**: keep last valid mesh dimmed, attach errors to the
   offending element, disable non-working export buttons with reasons. *M*
8. **Anchor paste/import** (2-col mm + 3-col CSV with cm→mm) and the CSV
   units header. *S-M*
9. **Alias cleanup**: drop `inflectionPolicy: 'allow'`/"Free" now; drop
   `cornerRatio` after (1) lands and tests migrate. *S*
10. **Docs** (mesher config-schema/geometry-contract FREEFORM sections,
    examples, WG feature list). *S-M*
11. Later: tangent-scale-truthful end handles + keyboard support; peripheral
    message cleanup; triple-gate collapse (with the two behavioural deltas
    ported first); backend STL/CSV; perf budgets; then Phase 1c sharp
    corners and Phase 3 optimizer.
