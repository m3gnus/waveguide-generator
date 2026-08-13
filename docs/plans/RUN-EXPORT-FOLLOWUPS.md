# Run-export follow-ups

Status: active plan containing only unfinished work, verified 2026-08-13.

## Already shipped

The completed-run menu, separate manual/automatic format preferences, snapshot-bound
geometry export, per-format failure reporting and retry, stable run-number filenames,
CSV union-frequency join, chart/directivity PNGs, on-axis FRD, VituixCAD H/V FRD sets,
stored polar phase, STEP solid/surface, STL, config, JSON, polar/impedance CSV, and
Fusion curve exports are implemented. Those are current behavior, not plan items.

## Remaining engineering

- [ ] Fold on-axis FRD, polar FRD, and the second directivity PNG request into the
      shared export dispatcher. `RunExportControl.tsx` currently marks all three as
      temporary seams.
- [ ] Decide whether a multi-format action remains multiple browser downloads or gains
      one server-built archive. If an archive is built, define a versioned manifest,
      member hashes, omission/failure states, ZIP64/size limits, cancellation, temp-file
      cleanup, and exactly one download per action.
- [ ] Give archived exports a job-snapshot transaction boundary so retention cannot
      remove results or mesh artifacts halfway through assembly.
- [ ] Reconcile VACS: it remains a preferences format and emits magnitude-only polar
      pressure with zero phase. Either specify a correct complex-pressure contract and
      test it or migrate/remove the format explicitly.
- [ ] Share one export artifact catalogue and numbering policy with CAD link instead of
      allowing run archives, `.wglink` bundles, and workspace folders to invent parallel
      identities.
- [ ] Measure realistic maximum result/mesh/archive sizes before choosing hard limits.

## Product decisions

- Should an archive include every available artifact or only the selected formats?
- Are server-rendered chart PNGs always included together, or independently selectable?
- Does automatic export ever create an archive, or only durable individual files?
- Which artifacts are long-lived records versus reproducible conveniences?

Coordinate the shared catalogue/numbering decision with the active CAD-link plan. The
workspace CAD documents stay outside this directory until that work closes.
