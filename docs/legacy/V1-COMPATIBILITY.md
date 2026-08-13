# Original-application compatibility

Status: maintained summary of deliberate compatibility and divergence. The original
179-row input audit and 377-line traceability inventory remain in Git history at
`748a6c6` and `f51a23c`; they are not current completion ledgers.

## Preserved boundaries

- `.cfg`, `.txt`, and legacy `.mwg` files use the ATH-style text grammar. Unknown keys,
  blocks, comments, and raw expressions survive parsing; ordered migrations normalize
  known retired fields.
- The editor covers the legacy design/solve input surface, including expression-capable
  values, FREEFORM point/station tables, polar controls, viewer preferences, results,
  exports, and job metadata.
- Original run databases and artifacts are imported read-only through the versioned
  migration tool. Recoverable snapshots remain reopenable, rerunnable, comparable, and
  exportable; unrecoverable legacy jobs are identified rather than guessed.
- Result phase, impedance, directivity, missing-value, and partial-success conventions
  have explicit current contracts and regression fixtures.

## Deliberate differences

- Dockview replaces the original fixed `resultsLayout`, `panelMode`, and
  `panelArrangement` settings with movable, resizable, persisted panels.
- FREEFORM stores a shared normalized `t` axis and solves tangent speed. Tangent-scale,
  per-anchor-strength, overshoot-policy, and `circle` station controls are migration
  inputs only. The current table editor does not reproduce the old graphical H/V
  visibility toggles or cross-section inset depth scrubber; those were view controls,
  not design data.
- R-OSSE plus enclosure is supported by the HornLab mesher. The original rejection was
  a limitation of its retired browser geometry engine and is not restored.
- Automatic symmetry validates the actual geometry. CircSym is an axisymmetric fast
  path under the selected solver, not a separate user-facing backend.
- STEP solid is the normal CAD export. A bare inner-surface STEP remains available, and
  STL remains available for tessellated workflows.
- The current app is the maintained product on `main`; the original application is the
  retired `origin/v1` line.

Any new compatibility claim should cite current code/tests and an immutable v1 Git
object. Do not turn an old `Required` or `OPEN` table cell into work without rechecking
the present implementation.
